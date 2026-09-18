import base64, ctypes, io, os, queue, subprocess, sys, tempfile, threading, time, wave
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
try:
    import customtkinter as ctk
    import psutil
except ImportError:
    ctk=None
    psutil=None
from pathlib import Path
import updater
try:
    import pc_optimizer
except ImportError:
    pc_optimizer=None
import json

APP_DIR=Path(__file__).resolve().parent
DEFAULT_REPO='Clemen5t/Coach_Fortnite_Ia'
VERSION=(APP_DIR/'VERSION').read_text().strip()

import mss
import numpy as np
import sounddevice as sd
from PIL import Image, ImageTk
from local_ai import LocalAI, usable, AdviceGate, needs_vision

VOICE_NAME='fr_FR-siwis-medium'
WHISPER_MODEL='small'
WHISPER_HINT=(
    "Acolyte, Fortnite, créatif, salon, Battle Royale, Zéro construction, build, edit, "
    "aim, inventaire, mini-carte, bouclier, soins, recharge, rotation, analyse mon jeu, "
    "te souviens-tu, tu te rappelles, son pseudo, son nom, épelle-moi son pseudo."
)

COLORS={
    'bg':'#060a16','panel':'#0f182c','panel2':'#141f38','panel3':'#091225',
    'border':'#263758','cyan':'#22d3ee','cyan2':'#0ea5e9','purple':'#7c3aed',
    'purple2':'#a855f7','gold':'#facc15','text':'#f8fafc','muted':'#91a4c5',
    'success':'#22c55e','danger':'#ef4444','warning':'#f59e0b','log':'#07101f'
}

SCENE_LABELS={
    'lobby':'SALON','creative':'CRÉATIF','match':'PARTIE','spectator':'SPECTATEUR / REPLAY',
    'menu':'MENU','loading':'CHARGEMENT','unknown':'CONTEXTE INCERTAIN'
}
SCENE_COLORS={
    'lobby':COLORS['purple2'],'creative':COLORS['cyan'],'match':COLORS['success'],
    'spectator':COLORS['warning'],'menu':COLORS['gold'],'loading':COLORS['muted'],'unknown':COLORS['muted']
}


def active_game():
    buf=ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(ctypes.windll.user32.GetForegroundWindow(),buf,512)
    return 'fortnite' in buf.value.lower()


class ToggleSwitch(tk.Frame):
    def __init__(self,parent,text,variable):
        super().__init__(parent,bg=COLORS['panel'])
        self.variable=variable
        self.canvas=tk.Canvas(self,width=42,height=22,bg=COLORS['panel'],highlightthickness=0,bd=0,cursor='hand2')
        self.canvas.pack(side='left')
        self.label=tk.Label(self,text=text,bg=COLORS['panel'],fg=COLORS['text'],font=('Segoe UI',9),cursor='hand2')
        self.label.pack(side='left',padx=(7,0))
        self.canvas.bind('<Button-1>',self.toggle); self.label.bind('<Button-1>',self.toggle)
        try:self.variable.trace_add('write',lambda *_:self.draw())
        except Exception:pass
        self.draw()

    def toggle(self,event=None):
        self.variable.set(not bool(self.variable.get())); self.draw()

    def draw(self):
        self.canvas.delete('all')
        on=bool(self.variable.get()); fill=COLORS['cyan2'] if on else '#32415f'; knob_x=30 if on else 12
        self.canvas.create_oval(2,3,20,21,fill=fill,outline='')
        self.canvas.create_rectangle(11,3,31,21,fill=fill,outline='')
        self.canvas.create_oval(22,3,40,21,fill=fill,outline='')
        self.canvas.create_oval(knob_x-7,6,knob_x+7,20,fill='#ffffff',outline='')


class VoiceEngine:
    def __init__(self,app_dir):
        self.root=Path(app_dir)
        self.data=self.root/'.voice'; self.data.mkdir(exist_ok=True)
        self.whisper_dir=self.root/'.whisper'; self.whisper_dir.mkdir(exist_ok=True)
        self.whisper=None; self.piper=None; self.lock=threading.RLock()

    @property
    def voice_model(self): return self.data/(VOICE_NAME+'.onnx')

    def ensure_tts(self,status=lambda x:None):
        if not self.voice_model.exists():
            status('Premier lancement : téléchargement de la voix IA française Piper…')
            cmd=[sys.executable,'-m','piper.download_voices',VOICE_NAME,'--data-dir',str(self.data)]
            result=subprocess.run(cmd,cwd=str(self.data),capture_output=True,text=True,timeout=300)
            if result.returncode!=0 or not self.voice_model.exists():
                detail=(result.stderr or result.stdout or 'voix introuvable')[-500:]
                raise RuntimeError('Téléchargement de la voix Piper impossible : '+detail)
        if self.piper is None:
            status('Chargement de la voix IA locale…')
            from piper import PiperVoice
            self.piper=PiperVoice.load(str(self.voice_model))

    def ensure_whisper(self,status=lambda x:None):
        if self.whisper is None:
            status('Chargement de Whisper Small local sur CPU…')
            from faster_whisper import WhisperModel
            self.whisper=WhisperModel(WHISPER_MODEL,device='cpu',compute_type='int8',download_root=str(self.whisper_dir))

    def prepare(self,status=lambda x:None):
        with self.lock:
            self.ensure_tts(status); self.ensure_whisper(status)

    def record_question(self,status=lambda x:None,level=lambda rms,peak,bands:None,seconds=4.5,device=None):
        with self.lock:
            self.ensure_whisper(status); status('🎙️ Parle maintenant…')
            rate=16000; block=512; chunks=[]
            def callback(indata,frames,timing,flags):
                samples=np.asarray(indata[:,0],dtype=np.float32).copy(); chunks.append(samples)
                rms=float(np.sqrt(np.mean(samples*samples))) if len(samples) else 0.0
                peak=float(np.max(np.abs(samples))) if len(samples) else 0.0
                bands=[]
                if len(samples):
                    window=np.hanning(len(samples)); spec=np.abs(np.fft.rfft(samples*window)); freq=np.fft.rfftfreq(len(samples),1/rate)
                    for lo,hi in ((80,180),(180,350),(350,700),(700,1400),(1400,2800),(2800,5000),(5000,7500)):
                        mask=(freq>=lo)&(freq<hi); bands.append(float(np.mean(spec[mask])) if np.any(mask) else 0.0)
                    m=max(bands) if bands else 0.0
                    if m>0: bands=[min(1.0,v/m) for v in bands]
                else: bands=[0.0]*7
                level(rms,peak,bands)
            try:
                with sd.InputStream(device=device,samplerate=rate,channels=1,dtype='float32',blocksize=block,callback=callback):
                    end=time.monotonic()+seconds
                    while time.monotonic()<end: time.sleep(.03)
            except Exception as e:
                raise RuntimeError('Impossible d’ouvrir le microphone sélectionné : '+str(e)) from e
            level(0.0,0.0,[0.0]*7)
            samples=np.concatenate(chunks) if chunks else np.zeros(0,dtype=np.float32)
            if samples.size==0: raise RuntimeError('Aucun échantillon reçu du microphone sélectionné.')
            status('Transcription locale…')
            segments,_=self.whisper.transcribe(
                samples,language='fr',beam_size=3,best_of=1,vad_filter=True,
                condition_on_previous_text=False,temperature=0,initial_prompt=WHISPER_HINT)
            return ' '.join(s.text.strip() for s in segments if s.text.strip()).strip()

    def speak(self,text,status=lambda x:None):
        if not text:return
        with self.lock:
            self.ensure_tts(status); status('🔊 Acolyte répond…')
            fd,path=tempfile.mkstemp(suffix='.wav'); os.close(fd)
            try:
                with wave.open(path,'wb') as wav_file:self.piper.synthesize_wav(text,wav_file)
                with wave.open(path,'rb') as wav_file:
                    rate=wav_file.getframerate(); channels=wav_file.getnchannels(); width=wav_file.getsampwidth(); raw=wav_file.readframes(wav_file.getnframes())
                if width==2:data=np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768.0
                elif width==4:data=np.frombuffer(raw,dtype=np.int32).astype(np.float32)/2147483648.0
                else:raise RuntimeError('Format audio Piper non pris en charge.')
                if channels>1:data=data.reshape(-1,channels)
                sd.play(data,rate); sd.wait()
            finally:
                try:os.unlink(path)
                except OSError:pass



class ScoreRing(tk.Canvas):
    def __init__(self,parent,size=154,bg=COLORS['panel']):
        super().__init__(parent,width=size,height=size,bg=bg,highlightthickness=0,bd=0)
        self.size=size;self.score=0;self._draw()
    def set_score(self,score):
        self.score=max(0,min(100,int(score)));self._draw()
    def _draw(self):
        self.delete('all');pad=13;s=self.size
        self.create_arc(pad,pad,s-pad,s-pad,start=90,extent=-359,style='arc',width=12,outline='#263758')
        color=COLORS['success'] if self.score>=85 else COLORS['cyan'] if self.score>=70 else COLORS['warning'] if self.score>=55 else COLORS['danger']
        self.create_arc(pad,pad,s-pad,s-pad,start=90,extent=-3.59*self.score,style='arc',width=12,outline=color)
        self.create_text(s/2,s/2-8,text=str(self.score),fill=COLORS['text'],font=('Segoe UI',29,'bold'))
        self.create_text(s/2,s/2+23,text='/ 100',fill=COLORS['muted'],font=('Segoe UI',10,'bold'))

class PCPremiumUI:
    NAV=[
        ('dashboard','⌂','Tableau de bord'),('performance','⚡','Performances'),('network','↔','Réseau'),
        ('gpu','◈','Carte graphique'),('privacy','◉','Confidentialité'),('startup','↗','Démarrage'),
        ('games','🎮','Jeux'),('checkup','✓','Check-up'),('bios','◆','BIOS / RAM'),
        ('apps','▦','Logiciels'),('updates','↻','Mises à jour'),('usb','⌁','USB')]
    def __init__(self,parent,app_dir):
        self.parent=parent;self.app_dir=app_dir;self.busy=False;self.last_scan=None;self.last_benchmark=None
        self.option_vars={k:tk.BooleanVar(value=(k in ('game','captures','balanced'))) for k in pc_optimizer.OPTIONS}
        self.nav_buttons={};self.reco_widgets=[];self._last_net=None
        ctk.set_appearance_mode('dark')
        self.root=ctk.CTkFrame(parent,fg_color=COLORS['bg'],corner_radius=0)
        self.root.pack(fill='both',expand=True)
        self.root.grid_rowconfigure(1,weight=1);self.root.grid_columnconfigure(1,weight=1);self.root.grid_columnconfigure(2,minsize=330)
        self._build_sidebar();self._build_topbar();self._build_main();self._build_summary()
        self.show('dashboard')
        self.parent.after(350,self.scan_full)
        self.parent.after(1000,self._tick_live)

    def _build_sidebar(self):
        side=ctk.CTkFrame(self.root,width=236,fg_color='#0B1426',corner_radius=0)
        side.grid(row=0,column=0,rowspan=2,sticky='nsew');side.grid_propagate(False)
        brand=ctk.CTkFrame(side,fg_color='transparent');brand.pack(fill='x',padx=16,pady=(18,12))
        ctk.CTkLabel(brand,text='ACOLYTE',text_color=COLORS['text'],font=ctk.CTkFont(size=27,weight='bold')).pack(anchor='w')
        ctk.CTkLabel(brand,text='PERFORMANCE',text_color=COLORS['cyan'],font=ctk.CTkFont(size=16,weight='bold')).pack(anchor='w')
        ctk.CTkLabel(brand,text='Analyse • Optimise • Mesure',text_color=COLORS['muted'],font=ctk.CTkFont(size=10)).pack(anchor='w',pady=(3,8))
        ctk.CTkFrame(side,height=2,fg_color=COLORS['cyan']).pack(fill='x',padx=16,pady=(0,10))
        for key,icon,label in self.NAV:
            b=ctk.CTkButton(side,text=f'{icon}  {label}',anchor='w',height=42,corner_radius=11,
                fg_color='transparent',hover_color=COLORS['panel2'],text_color=COLORS['text'],
                font=ctk.CTkFont(size=13,weight='bold'),command=lambda k=key:self.show(k))
            b.pack(fill='x',padx=10,pady=2);self.nav_buttons[key]=b
        self.admin_badge=ctk.CTkLabel(side,text='● ADMIN : ...',text_color=COLORS['muted'],font=ctk.CTkFont(size=10,weight='bold'))
        self.admin_badge.pack(side='bottom',anchor='w',padx=16,pady=14)

    def _build_topbar(self):
        top=ctk.CTkFrame(self.root,fg_color='transparent')
        top.grid(row=0,column=1,columnspan=2,sticky='ew',padx=18,pady=(14,10));top.grid_columnconfigure(0,weight=1)
        title=ctk.CTkFrame(top,fg_color='transparent');title.grid(row=0,column=0,sticky='w')
        ctk.CTkLabel(title,text='OPTIMISATION PC',text_color=COLORS['text'],font=ctk.CTkFont(size=30,weight='bold')).pack(anchor='w')
        ctk.CTkLabel(title,text='Optimisation mesurée et restaurable pour ton PC gaming',text_color=COLORS['muted'],font=ctk.CTkFont(size=12)).pack(anchor='w')
        actions=ctk.CTkFrame(top,fg_color='transparent');actions.grid(row=0,column=1,sticky='e')
        self.scan_btn=ctk.CTkButton(actions,text='✦  SCAN COMPLET',command=self.scan_full,width=142,height=42,corner_radius=12,fg_color=COLORS['cyan2'])
        self.scan_btn.pack(side='left',padx=4)
        self.optimize_btn=ctk.CTkButton(actions,text='⚡  OPTIMISER',command=self.optimize_smart,width=134,height=42,corner_radius=12,fg_color=COLORS['purple'])
        self.optimize_btn.pack(side='left',padx=4)
        self.bench_btn=ctk.CTkButton(actions,text='◫  BENCHMARK',command=self.benchmark,width=132,height=42,corner_radius=12,fg_color='#1675E0')
        self.bench_btn.pack(side='left',padx=4)
        self.restore_btn=ctk.CTkButton(actions,text='↶  RESTAURER',command=self.restore,width=122,height=42,corner_radius=12,fg_color=COLORS['danger'])
        self.restore_btn.pack(side='left',padx=4)

    def _build_main(self):
        main=ctk.CTkFrame(self.root,fg_color='transparent')
        main.grid(row=1,column=1,sticky='nsew',padx=(18,10),pady=(0,16))
        main.grid_rowconfigure(2,weight=1);main.grid_columnconfigure(0,weight=1)
        kpis=ctk.CTkFrame(main,fg_color='transparent');kpis.grid(row=0,column=0,sticky='ew',pady=(0,9))
        for i in range(4):kpis.grid_columnconfigure(i,weight=1,uniform='kpi')
        self.kpis={}
        for i,(key,title,accent) in enumerate([
            ('cpu','CPU',COLORS['cyan']),('gpu','GPU',COLORS['purple2']),('ram','RAM',COLORS['success']),('net','RÉSEAU',COLORS['gold'])]):
            box=ctk.CTkFrame(kpis,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#203354')
            box.grid(row=0,column=i,sticky='ew',padx=(0 if i==0 else 5,0 if i==3 else 5))
            ctk.CTkLabel(box,text=title,text_color=COLORS['muted'],font=ctk.CTkFont(size=11,weight='bold')).pack(anchor='w',padx=15,pady=(12,2))
            value=ctk.CTkLabel(box,text='—',text_color=COLORS['text'],font=ctk.CTkFont(size=22,weight='bold'))
            value.pack(anchor='w',padx=15)
            sub=ctk.CTkLabel(box,text='Analyse en attente',text_color=accent,font=ctk.CTkFont(size=10))
            sub.pack(anchor='w',padx=15,pady=(2,12));self.kpis[key]=(value,sub)

        self.section_header=ctk.CTkFrame(main,fg_color='transparent');self.section_header.grid(row=1,column=0,sticky='ew',pady=(3,8))
        self.section_title=ctk.CTkLabel(self.section_header,text='',text_color=COLORS['text'],font=ctk.CTkFont(size=23,weight='bold'))
        self.section_title.pack(side='left')
        self.section_badge=ctk.CTkLabel(self.section_header,text='LOCAL',fg_color='#173354',corner_radius=999,text_color=COLORS['cyan'],font=ctk.CTkFont(size=9,weight='bold'),padx=9,pady=3)
        self.section_badge.pack(side='left',padx=10)

        self.content=ctk.CTkScrollableFrame(main,fg_color='#0A1324',corner_radius=15,border_width=1,border_color='#1E3151')
        self.content.grid(row=2,column=0,sticky='nsew')

        self.tabs=ctk.CTkTabview(main,height=150,fg_color=COLORS['panel'],segmented_button_fg_color='#111E34',
            segmented_button_selected_color=COLORS['purple'],corner_radius=15)
        self.tabs.grid(row=3,column=0,sticky='ew',pady=(9,0))
        self.summary_text=self._text_tab('Résumé')
        self.details_text=self._text_tab('Détails')
        self.log_text=self._text_tab('Journal')
        self.log('Acolyte Performance prêt.')

    def _text_tab(self,name):
        tab=self.tabs.add(name)
        box=ctk.CTkTextbox(tab,height=88,fg_color='#07101F',text_color='#DCEBFF',corner_radius=10,font=('Consolas',10))
        box.pack(fill='both',expand=True,padx=5,pady=5);return box

    def _build_summary(self):
        side=ctk.CTkFrame(self.root,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#203354')
        side.grid(row=1,column=2,sticky='nsew',padx=(0,18),pady=(0,16))
        ctk.CTkLabel(side,text='SANTÉ DU PC',text_color=COLORS['muted'],font=ctk.CTkFont(size=10,weight='bold')).pack(anchor='w',padx=16,pady=(16,5))
        self.ring=ScoreRing(side,150,COLORS['panel']);self.ring.pack(pady=(0,2))
        self.score_state=ctk.CTkLabel(side,text='Scan en cours…',text_color=COLORS['cyan'],font=ctk.CTkFont(size=13,weight='bold'))
        self.score_state.pack()
        self.scan_progress=ctk.CTkProgressBar(side,height=8,corner_radius=999,progress_color=COLORS['cyan2'],fg_color='#263758')
        self.scan_progress.pack(fill='x',padx=22,pady=(10,2));self.scan_progress.set(0)
        ctk.CTkFrame(side,height=1,fg_color='#24395D').pack(fill='x',padx=16,pady=14)
        ctk.CTkLabel(side,text='RECOMMANDATIONS',text_color=COLORS['text'],font=ctk.CTkFont(size=12,weight='bold')).pack(anchor='w',padx=16)
        self.reco_frame=ctk.CTkFrame(side,fg_color='transparent');self.reco_frame.pack(fill='x',padx=12,pady=8)
        self._render_recos([])
        ctk.CTkFrame(side,height=1,fg_color='#24395D').pack(fill='x',padx=16,pady=10)
        self.quick_state=ctk.CTkTextbox(side,height=145,fg_color='#08111F',corner_radius=10,text_color=COLORS['muted'],font=('Segoe UI',9))
        self.quick_state.pack(fill='x',padx=14,pady=(0,14));self._set_quick_state('Analyse du système en attente.')

    def _set_quick_state(self,text):
        self.quick_state.configure(state='normal');self.quick_state.delete('1.0','end');self.quick_state.insert('end',text);self.quick_state.configure(state='disabled')

    def _clear(self):
        for child in self.content.winfo_children():child.destroy()

    def show(self,key):
        if self.busy:return
        self.current=key
        for k,b in self.nav_buttons.items():
            b.configure(fg_color=COLORS['purple'] if k==key else 'transparent')
        label=next((x[2] for x in self.NAV if x[0]==key),key)
        self.section_title.configure(text=label.upper())
        self._clear()
        getattr(self,'_page_'+key,self._page_generic)()

    def _hero(self,title,desc,accent=None):
        accent=accent or COLORS['cyan']
        box=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#203354')
        box.pack(fill='x',padx=10,pady=(10,8))
        bar=ctk.CTkFrame(box,width=5,fg_color=accent,corner_radius=4);bar.pack(side='left',fill='y',padx=(0,12),pady=12)
        txt=ctk.CTkFrame(box,fg_color='transparent');txt.pack(side='left',fill='x',expand=True,pady=12)
        ctk.CTkLabel(txt,text=title,text_color=COLORS['text'],font=ctk.CTkFont(size=19,weight='bold')).pack(anchor='w')
        ctk.CTkLabel(txt,text=desc,text_color=COLORS['muted'],font=ctk.CTkFont(size=11),wraplength=840,justify='left').pack(anchor='w',pady=(3,0))

    def _action_card(self,title,desc,status='Disponible',impact='Mesuré',risk='Faible',option=None,command=None,button='OUVRIR'):
        card=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#1C3153')
        card.pack(fill='x',padx=10,pady=5)
        left=ctk.CTkFrame(card,fg_color='transparent');left.pack(side='left',fill='both',expand=True,padx=14,pady=12)
        ctk.CTkLabel(left,text=title,text_color=COLORS['text'],font=ctk.CTkFont(size=15,weight='bold')).pack(anchor='w')
        ctk.CTkLabel(left,text=desc,text_color=COLORS['muted'],font=ctk.CTkFont(size=10),wraplength=700,justify='left').pack(anchor='w',pady=(3,7))
        tags=ctk.CTkFrame(left,fg_color='transparent');tags.pack(anchor='w')
        for text,color in [(status,'#15476A'),('Impact '+impact,'#594317'),('Risque '+risk,'#174B3A')]:
            ctk.CTkLabel(tags,text=text,fg_color=color,corner_radius=999,text_color='white',font=ctk.CTkFont(size=8,weight='bold'),padx=8,pady=2).pack(side='left',padx=(0,5))
        right=ctk.CTkFrame(card,fg_color='transparent');right.pack(side='right',padx=12,pady=12)
        if option:
            sw=ctk.CTkSwitch(right,text='Sélectionner',variable=self.option_vars[option],progress_color=COLORS['cyan2'],font=ctk.CTkFont(size=9))
            sw.pack(pady=(0,7))
        if command:
            ctk.CTkButton(right,text=button,command=command,width=110,height=32,corner_radius=9,fg_color=COLORS['purple']).pack()

    def _button_row(self,items):
        row=ctk.CTkFrame(self.content,fg_color='transparent');row.pack(fill='x',padx=10,pady=6)
        for text,cmd,color in items:
            ctk.CTkButton(row,text=text,command=cmd,fg_color=color,height=34,corner_radius=9).pack(side='left',padx=(0,6))

    def _page_dashboard(self):
        self._hero('Centre de contrôle','Une vue unique du matériel, de Windows, du réseau et des recommandations prioritaires.',COLORS['cyan'])
        self._button_row([('✦ SCAN COMPLET',self.scan_full,COLORS['cyan2']),('◫ BENCHMARK RÉSEAU',self.benchmark,'#1675E0'),('⚡ OPTIMISER INTELLIGENT',self.optimize_smart,COLORS['purple'])])

        strip=ctk.CTkFrame(self.content,fg_color='transparent');strip.pack(fill='x',padx=10,pady=(2,8))
        for i in range(3):strip.grid_columnconfigure(i,weight=1,uniform='dash')
        cards=[
            ('OPTIMISATIONS',str(len(pc_optimizer.recommended_options(self.last_scan))) if self.last_scan else '—','Réglages sûrs recommandés',COLORS['purple2']),
            ('JEUX DÉTECTÉS',str(len(self.last_scan.get('games') or [])) if self.last_scan else '—','Profils locaux détectés',COLORS['cyan']),
            ('DÉMARRAGE',str(self.last_scan.get('startup_count')) if self.last_scan and self.last_scan.get('startup_count') is not None else '—','Entrées Run détectées',COLORS['gold'])]
        for i,(title,value,sub,accent) in enumerate(cards):
            box=ctk.CTkFrame(strip,fg_color=COLORS['panel'],corner_radius=14,border_width=1,border_color='#203354')
            box.grid(row=0,column=i,sticky='ew',padx=(0 if i==0 else 5,0 if i==2 else 5))
            ctk.CTkLabel(box,text=title,text_color=COLORS['muted'],font=ctk.CTkFont(size=9,weight='bold')).pack(anchor='w',padx=13,pady=(10,1))
            ctk.CTkLabel(box,text=value,text_color=accent,font=ctk.CTkFont(size=24,weight='bold')).pack(anchor='w',padx=13)
            ctk.CTkLabel(box,text=sub,text_color=COLORS['muted'],font=ctk.CTkFont(size=9)).pack(anchor='w',padx=13,pady=(1,10))

        if self.last_scan:
            if not self.last_scan.get('recommendations'):
                self._action_card('Configuration saine','Aucune anomalie prioritaire détectée par les contrôles actuels. Continue avec un benchmark avant/après pour mesurer les gains.','OK','Mesuré','Nul')
            for rec in self.last_scan.get('recommendations',[])[:5]:
                self._action_card(rec['title'],rec['detail'],'Recommandé','Variable','Faible',
                    option=rec.get('option'),command=(lambda sec=rec.get('section'):self.show(sec)) if rec.get('section') else None,button='VOIR')
        else:
            self._action_card('Scan matériel & Windows','Lance un scan complet pour remplir le score, identifier les réglages utiles et éviter les tweaks inutiles.','À lancer','Élevé','Nul',command=self.scan_full,button='SCANNER')

    def _page_performance(self):
        self._hero('Performances gaming','Réglages sûrs et restaurables. Aucun overclocking, undervolt ou désactivation de sécurité automatique.',COLORS['purple2'])
        self._action_card('Mode Jeu Windows','Priorise mieux les ressources pour les jeux et réduit certaines activités de fond.','Recommandé','Faible','Faible','game')
        self._action_card('Captures Xbox Game Bar','Désactive les captures en arrière-plan si tu ne les utilises pas.','Optionnel','Moyen','Faible','captures')
        self._action_card('Plan d’alimentation Équilibré','Base stable recommandée pour Ryzen X3D ; les boosts CPU restent gérés par le firmware.','Recommandé','Moyen','Faible','balanced')
        self._button_row([('APPLIQUER LA SÉLECTION',self.apply_selected,COLORS['purple']),('Mode Jeu Windows',lambda:self._open('game'),COLORS['panel2']),('Alimentation',lambda:self._open('power'),COLORS['panel2'])])

    def _page_network(self):
        self._hero('Réseau & latence','Ping ICMP vers la box et Internet, diagnostic de la carte, RSS/RSC, DNS et économie d’énergie.',COLORS['cyan'])
        self._action_card('Benchmark réseau','Mesure passerelle, Cloudflare et Google avec ping, jitter et réponses perdues.','Mesurable','Élevé','Nul',command=self.benchmark,button='TESTER')
        self._action_card('Diagnostic carte réseau','Lit la liaison, la passerelle, les DNS, RSS/RSC et les options d’alimentation sans rien modifier.','Lecture seule','Moyen','Nul',command=lambda:self._diagnostic('network'),button='ANALYSER')

    def _page_gpu(self):
        self._hero('Carte graphique','Détection du GPU principal, version du pilote et accès aux outils officiels.',COLORS['purple2'])
        self._action_card('Pilotes GPU','Affiche les pilotes installés. La présence d’une mise à jour est vérifiée chez le fabricant.','Lecture seule','Élevé','Nul',command=lambda:self._diagnostic('gpu'),button='LIRE')
        self._button_row([('AMD Adrenalin / pilotes',lambda:self._open('amd'),COLORS['purple']),('Graphiques Windows',lambda:self._open('graphics'),COLORS['panel2']),('NVIDIA',lambda:self._open('nvidia'),COLORS['panel2'])])

    def _page_privacy(self):
        self._hero('Confidentialité Windows','Réduit certains contenus promotionnels sans désactiver Defender, le pare-feu, Windows Update ou les protections système.',COLORS['cyan'])
        self._action_card('Identifiant publicitaire','Désactive l’identifiant publicitaire du compte courant.','Restaurable','Faible','Faible','ads')
        self._action_card('Suggestions promotionnelles','Réduit certaines suggestions Windows pour le compte courant.','Restaurable','Faible','Faible','suggestions')
        self._button_row([('APPLIQUER LA SÉLECTION',self.apply_selected,COLORS['purple']),('Paramètres confidentialité',lambda:self._open('privacy'),COLORS['panel2'])])

    def _page_startup(self):
        self._hero('Démarrage','Inventorie les entrées Run du compte actuel. Retire uniquement ce que tu reconnais.',COLORS['gold'])
        self._button_row([('LISTER LES ENTRÉES',self.load_startup,COLORS['cyan2']),('Paramètres Démarrage',lambda:self._open('startup'),COLORS['panel2'])])

    def _page_games(self):
        self._hero('Jeux détectés','Détection locale via Epic Games et chemins connus. Aucun fichier de jeu n’est modifié.',COLORS['purple'])
        self._button_row([('DÉTECTER LES JEUX',self.load_games,COLORS['purple'])])

    def _page_checkup(self):
        self._hero('Check-up système','Espace disque et fichiers temporaires. Le nettoyage ne touche qu’au TEMP utilisateur ancien de plus de 7 jours.',COLORS['success'])
        self._action_card('Analyse entretien','Compte les temporaires et vérifie l’espace libre des disques.','Lecture seule','Moyen','Nul',command=lambda:self._diagnostic('checkup'),button='ANALYSER')
        self._action_card('Nettoyage prudent','Supprime uniquement les fichiers temporaires utilisateur anciens et non verrouillés.','Action locale','Faible','Faible',command=self.cleanup_temp,button='NETTOYER')

    def _page_bios(self):
        self._hero('BIOS / RAM','Diagnostic uniquement : vitesse RAM configurée, profil mémoire à vérifier, UEFI et virtualisation.',COLORS['warning'])
        self._action_card('EXPO / XMP & mémoire','Compare la vitesse configurée avec la vitesse annoncée par les barrettes. Aucun changement BIOS automatique.','Important','Élevé','Nul',command=lambda:self._diagnostic('bios'),button='ANALYSER')
        self._button_row([('Support MSI B650 Gaming Plus WiFi',lambda:self._open('board'),COLORS['panel2'])])

    def _page_apps(self):
        self._hero('Logiciels','Liste fermée d’applications Windows facultatives. La désinstallation est séparée de la restauration des tweaks.',COLORS['gold'])
        self._button_row([('LISTER LES APPS PROPOSÉES',self.load_apps,COLORS['cyan2']),('Toutes les applications',lambda:self._open('apps'),COLORS['panel2']),('Microsoft Store',lambda:self._open('store'),COLORS['panel2'])])

    def _page_updates(self):
        self._hero('Mises à jour','Accès direct aux sources officielles. Acolyte ne prétend pas qu’un pilote est à jour sans vérification.',COLORS['cyan'])
        self._button_row([('Windows Update',lambda:self._open('updates'),COLORS['cyan2']),('AMD',lambda:self._open('amd'),COLORS['purple']),('NVIDIA',lambda:self._open('nvidia'),COLORS['panel2']),('Intel',lambda:self._open('intel'),COLORS['panel2'])])

    def _page_usb(self):
        self._hero('USB & périphériques','Diagnostic de périphériques et test optionnel sans suspension USB sur secteur. Aucun gain de latence n’est garanti.',COLORS['cyan'])
        self._action_card('Suspension sélective USB','À tester uniquement en cas de déconnexions de périphériques.','Dépannage','Faible','Faible','usb')
        self._button_row([('LISTER LES PÉRIPHÉRIQUES',lambda:self._diagnostic('usb'),COLORS['cyan2']),('APPLIQUER LA SÉLECTION',self.apply_selected,COLORS['purple'])])

    def _page_generic(self):self._hero('Section','Contenu en cours de chargement.')

    def _open(self,name):
        try:pc_optimizer.open_panel(name)
        except Exception as exc:self._error('Ouverture',exc)

    def _diagnostic(self,category):
        self._run('Diagnostic '+category,lambda:pc_optimizer.diagnostics(category),lambda x:self._show_result('Diagnostic '+category,x))

    def scan_full(self):
        try:
            self.scan_progress.configure(mode='indeterminate');self.scan_progress.start()
            self.score_state.configure(text='Analyse du système…',text_color=COLORS['cyan'])
        except Exception:pass
        self._run('Scan complet',pc_optimizer.full_scan,self._scan_done)

    def _scan_done(self,data):
        try:
            self.scan_progress.stop();self.scan_progress.configure(mode='determinate');self.scan_progress.set(1)
        except Exception:pass
        self.last_scan=data;system=data.get('system',{});network=data.get('network',{})
        self.admin_badge.configure(text='● ADMIN : '+('OUI' if system.get('admin') else 'NON'),text_color=COLORS['success'] if system.get('admin') else COLORS['warning'])
        self.kpis['cpu'][0].configure(text=self._short_cpu(system.get('cpu','—')))
        self.kpis['gpu'][0].configure(text=self._short_gpu(system.get('gpu','—')))
        self.kpis['ram'][0].configure(text=f"{system.get('ram','—')} Go")
        self.kpis['net'][0].configure(text=str(network.get('link') or system.get('link') or '—'))
        self.kpis['cpu'][1].configure(text='Charge live en cours')
        self.kpis['gpu'][1].configure(text=str(system.get('gpu','—'))[:38])
        self.kpis['ram'][1].configure(text='Utilisation live en cours')
        self.kpis['net'][1].configure(text=str(network.get('name') or system.get('nic') or 'Interface active'))
        score=int(data.get('score',0));self.ring.set_score(score)
        state='EXCELLENT' if score>=90 else 'BON ÉTAT' if score>=75 else 'À OPTIMISER' if score>=60 else 'ATTENTION'
        self.score_state.configure(text=state,text_color=COLORS['success'] if score>=85 else COLORS['cyan'] if score>=70 else COLORS['warning'])
        self._render_recos(data.get('recommendations',[]))
        opts=set(pc_optimizer.recommended_options(data))
        for key in ('game','captures','balanced'):self.option_vars[key].set(key in opts)
        games=data.get('games') or [];startup=data.get('startup_count')
        self._set_quick_state(f"Version : {VERSION}\nAdministrateur : {'oui' if system.get('admin') else 'non'}\nJeux détectés : {len(games)}\nDémarrage Run : {startup if startup is not None else 'inconnu'}\nGPU : {system.get('gpu','?')}\nRéseau : {network.get('link','?')}")
        self._set_text(self.summary_text,self._scan_summary(data))
        self._set_text(self.details_text,json.dumps(data,ensure_ascii=False,indent=2))
        if self.current=='dashboard':self.show('dashboard')

    def _scan_summary(self,data):
        lines=[f"Score configuration : {data.get('score',0)}/100",'']
        for x in data.get('positives',[]):lines.append('✓ '+x)
        for x in data.get('recommendations',[]):lines.append('! '+x.get('title','')+' — '+x.get('detail',''))
        return '\n'.join(lines)

    def _render_recos(self,items):
        for w in self.reco_frame.winfo_children():w.destroy()
        if not items:
            ctk.CTkLabel(self.reco_frame,text='Lance un scan complet pour obtenir des recommandations.',text_color=COLORS['muted'],wraplength=245,justify='left',font=ctk.CTkFont(size=9)).pack(anchor='w',pady=3)
            return
        for rec in items[:4]:
            color=COLORS['warning'] if rec.get('level')=='important' else COLORS['cyan']
            row=ctk.CTkFrame(self.reco_frame,fg_color='#0A172A',corner_radius=9)
            row.pack(fill='x',pady=3)
            ctk.CTkLabel(row,text='●',text_color=color,font=ctk.CTkFont(size=11)).pack(side='left',padx=(8,5),pady=8)
            ctk.CTkLabel(row,text=rec.get('title','À vérifier'),text_color=COLORS['text'],font=ctk.CTkFont(size=9,weight='bold'),wraplength=200,justify='left').pack(side='left',fill='x',expand=True,pady=8)

    def benchmark(self):
        self._run('Benchmark réseau',pc_optimizer.benchmark,self._benchmark_done)

    def _benchmark_done(self,rows):
        text=pc_optimizer.format_benchmark(rows);previous=self.last_benchmark;self.last_benchmark=rows
        summary=text
        if previous:
            def avg_public(data):
                vals=[r.get('avg') for r in data if r.get('status')=='ok' and r.get('host') in ('1.1.1.1','8.8.8.8') and r.get('avg') is not None]
                return sum(vals)/len(vals) if vals else None
            a=avg_public(previous);b=avg_public(rows)
            if a is not None and b is not None:summary+=f"\n\nComparaison avec le test précédent : {a:.1f} → {b:.1f} ms ({b-a:+.1f} ms)."
        self._show_result('Benchmark réseau',summary)

    def apply_selected(self):
        opts=[k for k,v in self.option_vars.items() if v.get()]
        if not opts:return messagebox.showinfo('Acolyte Performance','Sélectionne au moins un réglage.')
        details='\n'.join('• '+pc_optimizer.OPTIONS[k][0] for k in opts)
        if not messagebox.askyesno('Appliquer la sélection',details+'\n\nUne sauvegarde durable est créée avant modification. Continuer ?'):return
        self._run('Application des réglages',lambda:pc_optimizer.apply_selected(opts,self.app_dir),lambda x:self._after_mutation('Optimisation',x))

    def optimize_smart(self):
        if not self.last_scan:
            return self._run('Scan avant optimisation',pc_optimizer.full_scan,self._smart_after_scan)
        self._smart_after_scan(self.last_scan)

    def _smart_after_scan(self,scan):
        self.last_scan=scan;opts=pc_optimizer.recommended_options(scan)
        if not opts:return messagebox.showinfo('Optimisation intelligente','Les réglages sûrs suivis par Acolyte sont déjà dans l’état recommandé.')
        details='\n'.join('• '+pc_optimizer.OPTIONS[k][0] for k in opts)
        if not messagebox.askyesno('Optimisation intelligente','Acolyte recommande :\n\n'+details+'\n\nAucun tweak réseau agressif, BIOS, overclock ou sécurité ne sera appliqué. Continuer ?'):return
        self._run('Optimisation intelligente',lambda:pc_optimizer.apply_selected(opts,self.app_dir),lambda x:self._after_mutation('Optimisation intelligente',x))

    def _after_mutation(self,title,result):
        self._show_result(title,result)
        self.parent.after(250,self.scan_full)

    def restore(self):
        if not messagebox.askyesno('Restaurer','Rétablir les réglages suivis par Acolyte depuis la sauvegarde initiale ?'):return
        self._run('Restauration',lambda:pc_optimizer.restore(self.app_dir),lambda x:self._after_mutation('Restauration',x))

    def cleanup_temp(self):
        if not messagebox.askyesno('Nettoyage prudent','Supprimer uniquement les fichiers TEMP utilisateur vieux de plus de 7 jours ?'):return
        self._run('Nettoyage TEMP',lambda:pc_optimizer.cleanup_temp(7),lambda x:self._show_result('Check-up',x))

    def load_startup(self):
        self._run('Démarrage',pc_optimizer.startup_items,self._render_startup)
    def _render_startup(self,rows):
        self._clear();self._hero('Applications au démarrage',f'{len(rows)} entrée(s) Run détectée(s). Retire uniquement ce que tu reconnais.',COLORS['gold'])
        for row in rows[:24]:
            self._action_card(row['name'],str(row.get('command',''))[:170],'Installé','Variable','À vérifier',
                command=lambda n=row['name']:self._disable_startup(n),button='RETIRER')
        self._set_text(self.details_text,json.dumps(rows,ensure_ascii=False,indent=2))
    def _disable_startup(self,name):
        if messagebox.askyesno('Démarrage','Retirer '+name+' du démarrage automatique ?\nLe programme restera installé et Restaurer pourra réactiver cette entrée.'):
            self._run('Démarrage '+name,lambda:pc_optimizer.disable_startup(self.app_dir,name),lambda x:self._show_result('Démarrage',x))

    def load_games(self):
        self._run('Détection des jeux',pc_optimizer.detected_games,self._render_games)
    def _render_games(self,rows):
        self._clear();self._hero('Jeux détectés',f'{len(rows)} jeu(x) détecté(s) localement.',COLORS['purple'])
        if not rows:self._action_card('Aucun jeu détecté','Ajoute les autres launchers plus tard ou vérifie l’installation Epic.','Information','Nul','Nul')
        for row in rows:self._action_card(row.get('name','Jeu'),row.get('launcher','')+' • '+row.get('path',''),'Détecté','Profil futur','Nul')
        self._set_text(self.details_text,json.dumps(rows,ensure_ascii=False,indent=2))

    def load_apps(self):
        self._run('Applications facultatives',pc_optimizer.removable_apps,self._render_apps)
    def _render_apps(self,rows):
        self._clear();self._hero('Applications Windows facultatives',f'{len(rows)} application(s) reconnue(s). La désinstallation ne fait pas partie de Restaurer.',COLORS['gold'])
        for row in rows:
            self._action_card(row['name'],row['package'],'Installée','Faible','Irréversible par Acolyte',
                command=lambda p=row['package'],n=row['name']:self._remove_app(p,n),button='DÉSINSTALLER')
        self._set_text(self.details_text,json.dumps(rows,ensure_ascii=False,indent=2))
    def _remove_app(self,package,name):
        if messagebox.askyesno('Désinstaller',f'Désinstaller {name} pour ton compte ?\nLes données locales peuvent être supprimées et Restaurer ne réinstalle pas l’application.'):
            self._run('Désinstallation '+name,lambda:pc_optimizer.remove_app(self.app_dir,package),lambda x:self._show_result('Logiciels',x))

    def _show_result(self,title,result):
        text=result if isinstance(result,str) else json.dumps(result,ensure_ascii=False,indent=2)
        self._set_text(self.summary_text,title+'\n\n'+text)
        self._set_text(self.details_text,text)
        self.tabs.set('Résumé')

    def _run(self,label,func,callback=None):
        if self.busy:return
        self.busy=True;self._set_busy(True);self.log('[START] '+label)
        def work():
            try:
                value=func();self.parent.after(0,lambda:self._finish(label,value,callback))
            except Exception as exc:self.parent.after(0,lambda exc=exc:self._error(label,exc))
        threading.Thread(target=work,daemon=True).start()

    def _finish(self,label,value,callback):
        self.busy=False;self._set_busy(False);self.log('[OK] '+label)
        if callback:callback(value)
        elif value is not None:self._show_result(label,value)

    def _error(self,label,exc):
        try:
            self.scan_progress.stop();self.scan_progress.configure(mode='determinate');self.scan_progress.set(0)
        except Exception:pass
        self.busy=False;self._set_busy(False);self.log('[ERREUR] '+label+' : '+str(exc))
        messagebox.showerror('Acolyte Performance',label+' :\n'+str(exc))

    def _set_busy(self,on):
        state='disabled' if on else 'normal'
        for b in (self.scan_btn,self.optimize_btn,self.bench_btn,self.restore_btn):b.configure(state=state)
        self.section_badge.configure(text='ANALYSE…' if on else 'LOCAL',text_color=COLORS['warning'] if on else COLORS['cyan'])

    def log(self,text):
        self.log_text.configure(state='normal');self.log_text.insert('end',f"[{time.strftime('%H:%M:%S')}] {text}\n");self.log_text.see('end')

    def _set_text(self,box,text):
        box.configure(state='normal');box.delete('1.0','end');box.insert('end',str(text));box.see('1.0')

    def _short_cpu(self,name):
        n=str(name).replace('AMD ','').replace(' Processor','')
        return n[:28]
    def _short_gpu(self,name):
        return str(name).replace('AMD ','').replace('NVIDIA ','')[:28]

    def _tick_live(self):
        try:
            if psutil is not None:
                cpu=psutil.cpu_percent(interval=None);mem=psutil.virtual_memory()
                self.kpis['cpu'][1].configure(text=f'Charge {cpu:.0f}%')
                self.kpis['ram'][1].configure(text=f'Utilisée {mem.used/1024**3:.1f} Go • {mem.percent:.0f}%')
                io=psutil.net_io_counters();now=time.time()
                if self._last_net:
                    t,r,s=self._last_net;dt=max(.1,now-t)
                    down=(io.bytes_recv-r)*8/dt/1e6;up=(io.bytes_sent-s)*8/dt/1e6
                    self.kpis['net'][1].configure(text=f'↓ {down:.2f} Mb/s  ↑ {up:.2f} Mb/s')
                self._last_net=(now,io.bytes_recv,io.bytes_sent)
        except Exception:pass
        try:self.parent.after(1000,self._tick_live)
        except Exception:pass

class Coach:
    def __init__(self,root):
        self.root=root; self.events=queue.Queue(); self.stop_event=threading.Event(); self.voice_stop=threading.Event()
        self.worker=None; self.client=None; self.session_id=0; self.updating=False; self.restart_required=False
        self.voice_busy=False; self.voice=VoiceEngine(APP_DIR); self.history=[]; self.hotkey_listener=None
        try:self.saved_settings=updater.read_json(APP_DIR/'settings.json',{}) or {}
        except Exception:self.saved_settings={}

        self.build_theme()
        root.title('Acolyte Fortnite — créé par Clemen4t — '+VERSION)
        root.geometry('1200x900'); root.minsize(1060,780); root.configure(bg=COLORS['bg'])

        shell=tk.Frame(root,bg=COLORS['bg']); shell.pack(fill='both',expand=True)
        self.build_header(shell)
        nav=tk.Frame(shell,bg=COLORS['bg']); nav.pack(fill='x',padx=22,pady=(0,10))
        ttk.Button(nav,text='🎮  COACH FORTNITE',command=lambda:self.show_section('coach'),style='Primary.TButton').pack(side='left')
        ttk.Button(nav,text='⚡  OPTIMISATION PC',command=lambda:self.show_section('pc'),style='Cyan.TButton').pack(side='left',padx=8)

        self.section_host=tk.Frame(shell,bg=COLORS['bg']); self.section_host.pack(fill='both',expand=True)
        self.coach_section=tk.Frame(self.section_host,bg=COLORS['bg'])
        content=tk.Frame(self.coach_section,bg=COLORS['bg']); content.pack(fill='both',expand=True,padx=22,pady=(0,12))
        content.grid_columnconfigure(0,weight=3,uniform='main'); content.grid_columnconfigure(1,weight=2,uniform='main')
        content.grid_rowconfigure(0,weight=1)
        left=tk.Frame(content,bg=COLORS['bg']); left.grid(row=0,column=0,sticky='nsew',padx=(0,8))
        right=tk.Frame(content,bg=COLORS['bg']); right.grid(row=0,column=1,sticky='nsew',padx=(8,0))

        self.build_voice_card(left)
        self.build_response_card(left)
        self.build_log_card(left)
        self.build_auto_card(right)
        self.build_context_card(right)
        self.build_system_card(right)
        self.pc_section=tk.Frame(self.section_host,bg=COLORS['bg'])
        self.build_pc_section(self.pc_section)
        self.show_section('coach')
        self.build_footer(shell)

        self.refresh_microphones(initial=True)
        self.install_hotkey(); root.protocol('WM_DELETE_WINDOW',self.close); root.after(50,self.poll)

    def show_section(self,name):
        for frame in (self.coach_section,self.pc_section):frame.pack_forget()
        (self.pc_section if name=='pc' else self.coach_section).pack(fill='both',expand=True)

    def build_pc_section(self,parent):
        if pc_optimizer is None:
            tk.Label(parent,text='Module PC absent. Relance Acolyte après la mise à jour.',bg=COLORS['bg'],fg=COLORS['text']).pack(pady=30)
            return
        if ctk is None or psutil is None:
            tk.Label(parent,text='Interface Performance incomplète : lance Lancer.bat pour installer customtkinter et psutil.',bg=COLORS['bg'],fg=COLORS['warning']).pack(pady=30)
            return
        self.pc_ui=PCPremiumUI(parent,APP_DIR)

    def build_theme(self):
        style=ttk.Style()
        try:style.theme_use('clam')
        except tk.TclError:pass
        style.configure('Dark.TCombobox',fieldbackground=COLORS['panel3'],background=COLORS['panel3'],foreground=COLORS['text'],arrowcolor=COLORS['cyan'],bordercolor=COLORS['border'],lightcolor=COLORS['border'],darkcolor=COLORS['border'],padding=7)
        style.map('Dark.TCombobox',fieldbackground=[('readonly',COLORS['panel3'])],foreground=[('readonly',COLORS['text'])],selectbackground=[('readonly',COLORS['panel3'])],selectforeground=[('readonly',COLORS['text'])])
        style.configure('Dark.TEntry',fieldbackground=COLORS['panel3'],foreground=COLORS['text'],insertcolor=COLORS['text'],bordercolor=COLORS['border'],padding=6)
        for name,bg,fg in (
            ('Primary.TButton',COLORS['purple'],COLORS['text']),('Cyan.TButton',COLORS['cyan2'],COLORS['text']),
            ('Gold.TButton',COLORS['gold'],'#111827'),('Danger.TButton',COLORS['danger'],COLORS['text']),
            ('Ghost.TButton',COLORS['panel2'],COLORS['text'])):
            style.configure(name,background=bg,foreground=fg,borderwidth=0,padding=(14,9),font=('Segoe UI',10,'bold'))
            style.map(name,background=[('active',self.mix(bg,'#ffffff',0.14)),('disabled',COLORS['border'])],foreground=[('disabled',COLORS['muted'])])

    @staticmethod
    def mix(a,b,t):
        try:
            aa=[int(a[i:i+2],16) for i in (1,3,5)]; bb=[int(b[i:i+2],16) for i in (1,3,5)]
            return '#'+''.join(f'{round(x+(y-x)*t):02x}' for x,y in zip(aa,bb))
        except Exception:return a

    def card(self,parent,title,subtitle=None,accent=None,pady=(0,14),expand=False):
        accent=accent or COLORS['cyan']
        outer=tk.Frame(parent,bg=COLORS['border'])
        outer.pack(fill='both' if expand else 'x',expand=expand,pady=pady)
        inner=tk.Frame(outer,bg=COLORS['panel']); inner.pack(fill='both',expand=True,padx=1,pady=1)
        top=tk.Frame(inner,bg=COLORS['panel']); top.pack(fill='x',padx=16,pady=(13,8))
        tk.Frame(top,bg=accent,width=5,height=34).pack(side='left',padx=(0,10))
        heading=tk.Frame(top,bg=COLORS['panel']); heading.pack(side='left',fill='x',expand=True)
        tk.Label(heading,text=title,bg=COLORS['panel'],fg=COLORS['text'],font=('Segoe UI',13,'bold')).pack(anchor='w')
        if subtitle:tk.Label(heading,text=subtitle,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),wraplength=650,justify='left').pack(anchor='w',pady=(2,0))
        body=tk.Frame(inner,bg=COLORS['panel']); body.pack(fill='both',expand=True,padx=16,pady=(0,14))
        return body

    def build_header(self,parent):
        header=tk.Frame(parent,bg=COLORS['bg']); header.pack(fill='x',padx=22,pady=(16,12))
        logo=tk.Canvas(header,width=104,height=94,bg=COLORS['bg'],highlightthickness=0,bd=0); logo.pack(side='left')
        logo.create_polygon(52,4,96,25,87,78,52,91,17,78,8,25,fill='#111a36',outline=COLORS['purple2'],width=3)
        logo.create_polygon(52,12,85,30,79,68,52,80,25,68,19,30,fill='#081329',outline=COLORS['cyan'],width=2)
        logo.create_polygon(29,40,52,22,75,40,66,51,52,40,38,51,fill=COLORS['cyan'],outline='')
        logo.create_polygon(37,58,52,48,67,58,61,69,43,69,fill=COLORS['purple'],outline='')
        logo.create_text(52,64,text='AI',fill=COLORS['text'],font=('Segoe UI Black',15,'bold'))

        titlebox=tk.Frame(header,bg=COLORS['bg']); titlebox.pack(side='left',fill='x',expand=True,padx=14)
        tk.Label(titlebox,text='ACOLYTE FORTNITE',bg=COLORS['bg'],fg=COLORS['text'],font=('Segoe UI Black',27,'bold')).pack(anchor='w')
        tk.Label(titlebox,text='Ton coéquipier IA local • créé par Clemen4t',bg=COLORS['bg'],fg=COLORS['cyan'],font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(1,3))
        tk.Label(titlebox,text='Conversation naturelle par défaut • vision uniquement sur demande • 100% local',bg=COLORS['bg'],fg=COLORS['muted'],font=('Segoe UI',9)).pack(anchor='w')

        badges=tk.Frame(header,bg=COLORS['bg']); badges.pack(side='right',anchor='ne')
        tk.Label(badges,text=' v'+VERSION+' ',bg=COLORS['purple'],fg=COLORS['text'],font=('Segoe UI',9,'bold'),padx=8,pady=5).pack(side='right',padx=(6,0))
        tk.Label(badges,text=' 100% LOCAL ',bg=COLORS['gold'],fg='#111827',font=('Segoe UI',9,'bold'),padx=8,pady=5).pack(side='right')
        tk.Frame(parent,bg=COLORS['cyan'],height=2).pack(fill='x',padx=22,pady=(0,14))

    def build_voice_card(self,parent):
        body=self.card(parent,'ACOLYTE VOCAL','F8 → parle naturellement. Acolyte regarde l’écran seulement si ta demande le nécessite.',COLORS['purple'])
        self.mic_choice=tk.StringVar(value=str(self.saved_settings.get('microphone','')))
        self.mic_devices={}; self.mic_label=tk.StringVar(value='Micro : détection…'); self.mic_detail=tk.StringVar(value='')
        tk.Label(body,text='MICROPHONE',bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).pack(anchor='w')
        microw=tk.Frame(body,bg=COLORS['panel']); microw.pack(fill='x',pady=(5,4))
        self.mic_combo=ttk.Combobox(microw,textvariable=self.mic_choice,state='readonly',style='Dark.TCombobox')
        self.mic_combo.pack(side='left',fill='x',expand=True)
        ttk.Button(microw,text='↻ Actualiser',command=self.refresh_microphones,style='Ghost.TButton').pack(side='left',padx=(8,0))
        self.mic_combo.bind('<<ComboboxSelected>>',lambda e:self.on_microphone_changed())
        tk.Label(body,textvariable=self.mic_detail,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8),wraplength=650,justify='left').pack(anchor='w',pady=(0,8))

        actions=tk.Frame(body,bg=COLORS['panel']); actions.pack(fill='x')
        self.talk_button=ttk.Button(actions,text='🎙  PARLER AU COACH  •  F8',command=self.ask_voice,style='Primary.TButton'); self.talk_button.pack(side='left')
        ttk.Button(actions,text='Préparer la voix IA',command=self.prepare_voice,style='Cyan.TButton').pack(side='left',padx=8)
        self.voice_state=tk.StringVar(value='Prêt. F8 = parler au coach.')
        tk.Label(body,textvariable=self.voice_state,bg=COLORS['panel'],fg=COLORS['cyan'],font=('Segoe UI',10,'bold'),wraplength=650,justify='left').pack(anchor='w',pady=(10,6))

        meterrow=tk.Frame(body,bg=COLORS['panel']); meterrow.pack(fill='x')
        tk.Label(meterrow,textvariable=self.mic_label,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),width=24,anchor='w').pack(side='left')
        meterbox=tk.Frame(meterrow,bg=COLORS['panel']); meterbox.pack(side='left',fill='x',expand=True,padx=(8,0))
        self.meter=tk.Canvas(meterbox,height=38,highlightthickness=1,highlightbackground=COLORS['cyan2'],bg=COLORS['panel3'],bd=0)
        self.meter.pack(fill='x')
        self.audio_bars=[]
        for i in range(20):
            x1=6+i*23; x2=x1+15
            self.audio_bars.append(self.meter.create_rectangle(x1,30,x2,32,fill='#26334f',outline=''))
        legend=tk.Frame(meterbox,bg=COLORS['panel']); legend.pack(fill='x',pady=(3,0))
        tk.Label(legend,text='SILENCE',bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',7,'bold')).pack(side='left')
        tk.Label(legend,text='VOIX',bg=COLORS['panel'],fg=COLORS['cyan'],font=('Segoe UI',7,'bold')).pack(side='left',expand=True)
        tk.Label(legend,text='SATURATION',bg=COLORS['panel'],fg=COLORS['warning'],font=('Segoe UI',7,'bold')).pack(side='right')

    def build_response_card(self,parent):
        body=self.card(parent,'RÉPONSE DU COACH','Indique clairement si la réponse vient de la conversation ou d’une analyse visuelle.',COLORS['cyan'])
        self.status=tk.StringVar(value='Mode vocal prêt.'); self.metric=tk.StringVar(value='Aucune analyse en cours.')
        self.advice=tk.StringVar(value='Appuie sur F8 puis parle.'); self.scene_state=tk.StringVar(value='EN ATTENTE')
        info=tk.Frame(body,bg=COLORS['panel']); info.pack(fill='x',pady=(0,8))
        tk.Label(info,textvariable=self.status,bg=COLORS['panel'],fg=COLORS['success'],font=('Segoe UI',9,'bold')).pack(side='left')
        tk.Label(info,textvariable=self.metric,bg=COLORS['panel'],fg=COLORS['muted'],font=('Consolas',9)).pack(side='right')
        answer=tk.Frame(body,bg=COLORS['panel3'],highlightbackground=COLORS['purple'],highlightthickness=1); answer.pack(fill='x')
        topline=tk.Frame(answer,bg=COLORS['panel3']); topline.pack(fill='x',padx=14,pady=(11,2))
        tk.Label(topline,text='ACOLYTE',bg=COLORS['panel3'],fg=COLORS['purple2'],font=('Segoe UI',8,'bold')).pack(side='left')
        self.scene_badge=tk.Label(topline,textvariable=self.scene_state,bg=COLORS['border'],fg=COLORS['text'],font=('Segoe UI',8,'bold'),padx=8,pady=3)
        self.scene_badge.pack(side='right')
        self.advice_label=tk.Label(answer,textvariable=self.advice,bg=COLORS['panel3'],fg=COLORS['text'],font=('Segoe UI',15,'bold'),wraplength=675,justify='left')
        self.advice_label.pack(anchor='w',fill='x',padx=14,pady=(5,14))

    def build_log_card(self,parent):
        body=self.card(parent,'JOURNAL DE PARTIE','Tes questions et les réponses d’Acolyte.',COLORS['gold'],pady=(0,0),expand=True)
        self.log=tk.Text(body,height=8,wrap='word',bg=COLORS['log'],fg='#dbeafe',insertbackground=COLORS['text'],relief='flat',bd=0,font=('Consolas',9),padx=12,pady=10,selectbackground=COLORS['purple'])
        self.log.pack(fill='both',expand=True)
        self.log.tag_configure('you',foreground='#f8fafc',font=('Consolas',9,'bold'))
        self.log.tag_configure('ai',foreground=COLORS['cyan'],font=('Consolas',9,'bold'))
        self.log.tag_configure('auto',foreground=COLORS['gold'])
        self.log.tag_configure('muted',foreground=COLORS['muted'])
        self.log.insert('end','ACOLYTE prêt • F8 pour parler au coach\n','muted')

    def build_auto_card(self,parent):
        body=self.card(parent,'ANALYSE AUTOMATIQUE','Optionnelle. Garde un intervalle élevé pour protéger tes FPS.',COLORS['gold'])
        self.monitor=tk.IntVar(value=1); self.interval=tk.DoubleVar(value=4.0); self.expiry=tk.DoubleVar(value=2.5); self.minutes=tk.IntVar(value=5)
        self.onlygame=tk.BooleanVar(value=True); self.speak=tk.BooleanVar(value=True)
        self.preferences={'monitor':self.monitor,'interval':self.interval,'expiry':self.expiry,'minutes':self.minutes,'onlygame':self.onlygame,'speak':self.speak,'microphone':self.mic_choice}
        for name,var in self.preferences.items():
            if name in self.saved_settings and name!='microphone':
                try:var.set(self.saved_settings[name])
                except Exception:pass
        grid=tk.Frame(body,bg=COLORS['panel']); grid.pack(fill='x')
        for r,(label,var,suffix) in enumerate([('Écran',self.monitor,''),('Intervalle',self.interval,' s'),('Rejet après',self.expiry,' s'),('Durée',self.minutes,' min')]):
            tk.Label(grid,text=label.upper(),bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).grid(row=r,column=0,sticky='w',pady=5)
            ttk.Entry(grid,textvariable=var,width=12,style='Dark.TEntry').grid(row=r,column=1,sticky='e',padx=(18,4),pady=5)
            tk.Label(grid,text=suffix,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8)).grid(row=r,column=2,sticky='w')
        grid.columnconfigure(0,weight=1)
        ToggleSwitch(body,'Seulement quand Fortnite est au premier plan',self.onlygame).pack(anchor='w',pady=(8,3))
        ToggleSwitch(body,'Lire aussi les conseils auto avec Piper',self.speak).pack(anchor='w')
        row=tk.Frame(body,bg=COLORS['panel']); row.pack(fill='x',pady=(12,0))
        self.start_button=ttk.Button(row,text='▶ Démarrer auto',command=self.start,style='Cyan.TButton'); self.start_button.pack(side='left')
        ttk.Button(row,text='■ Arrêter',command=self.stop,style='Danger.TButton').pack(side='left',padx=8)
        ttk.Button(row,text='Aperçu écran',command=self.preview,style='Ghost.TButton').pack(side='left')

    def build_context_card(self,parent):
        body=self.card(parent,'MODE CONTEXTUEL','Conversation par défaut. La vision s’active seulement quand elle est utile.',COLORS['cyan'])
        self.context_big=tk.StringVar(value='CONVERSATION')
        self.context_observation=tk.StringVar(value='Aucune capture tant que tu ne demandes pas d’analyser ou regarder l’écran.')
        self.context_label=tk.Label(body,textvariable=self.context_big,bg=COLORS['panel3'],fg=COLORS['cyan'],font=('Segoe UI',12,'bold'),padx=12,pady=10,anchor='w')
        self.context_label.pack(fill='x')
        tk.Label(body,textvariable=self.context_observation,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),wraplength=380,justify='left').pack(anchor='w',pady=(8,8))
        tk.Label(body,text='Vision : « analyse mon jeu » • « regarde mon écran » • « je suis dans quel mode ? »',bg=COLORS['panel'],fg=COLORS['text'],font=('Segoe UI',8,'italic'),wraplength=380,justify='left').pack(anchor='w')

    def build_system_card(self,parent):
        body=self.card(parent,'SYSTÈME','Mises à jour et connexion au dépôt.',COLORS['purple'],pady=(0,0))
        tk.Label(body,text='Les modèles restent sur ton PC. Aucun abonnement ni clé API.',bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),wraplength=380,justify='left').pack(anchor='w',pady=(0,10))
        row=tk.Frame(body,bg=COLORS['panel']); row.pack(fill='x')
        self.update_button=ttk.Button(row,text='⬇ Mettre à jour',command=self.update_app,style='Gold.TButton'); self.update_button.pack(side='left')
        ttk.Button(row,text='GitHub',command=self.configure_repo,style='Ghost.TButton').pack(side='left',padx=8)
        statusbox=tk.Frame(body,bg=COLORS['panel3'],highlightbackground=COLORS['border'],highlightthickness=1); statusbox.pack(fill='x',pady=(12,0))
        tk.Label(statusbox,text='BUILD',bg=COLORS['panel3'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).pack(anchor='w',padx=12,pady=(9,0))
        tk.Label(statusbox,text='Acolyte '+VERSION,bg=COLORS['panel3'],fg=COLORS['cyan'],font=('Segoe UI',11,'bold')).pack(anchor='w',padx=12,pady=(2,2))
        tk.Label(statusbox,text='créé par Clemen4t',bg=COLORS['panel3'],fg=COLORS['gold'],font=('Segoe UI',9,'bold')).pack(anchor='w',padx=12,pady=(0,9))

    def build_footer(self,parent):
        footer=tk.Frame(parent,bg=COLORS['bg']); footer.pack(fill='x',padx=22,pady=(0,10))
        tk.Label(footer,text='ACOLYTE FORTNITE',bg=COLORS['bg'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).pack(side='left')
        tk.Label(footer,text='créé par Clemen4t',bg=COLORS['bg'],fg=COLORS['cyan'],font=('Segoe UI',8,'bold')).pack(side='right')

    def refresh_microphones(self,initial=False):
        previous=self.mic_choice.get().strip()
        try:
            devices=sd.query_devices(); values=[]; mapping={}
            try:default_input=int(sd.default.device[0])
            except Exception:default_input=None
            for index,dev in enumerate(devices):
                if int(dev.get('max_input_channels',0) or 0)<=0:continue
                name=str(dev.get('name','Microphone')).strip()
                try:host=str(sd.query_hostapis(int(dev.get('hostapi',0))).get('name','')).strip()
                except Exception:host=''
                label=f'{index} — {name}' + (f' [{host}]' if host else '')
                values.append(label); mapping[label]=index
            self.mic_devices=mapping; self.mic_combo['values']=values
            selected=previous if previous in mapping else ''
            if not selected and previous:
                old=previous.split(' — ',1)[-1].split(' [',1)[0].strip().lower()
                selected=next((x for x in values if x.split(' — ',1)[-1].split(' [',1)[0].strip().lower()==old),'')
            if not selected and default_input is not None:selected=next((label for label,idx in mapping.items() if idx==default_input),'')
            if not selected and values:selected=values[0]
            self.mic_choice.set(selected); self.update_mic_text(selected)
            if not initial:self.save_preferences()
        except Exception as e:
            self.mic_devices={}; self.mic_combo['values']=(); self.mic_choice.set(''); self.mic_label.set('Micro : erreur'); self.mic_detail.set('Impossible de détecter les périphériques audio.')
            if not initial:messagebox.showerror('Microphone',str(e))

    def update_mic_text(self,label):
        if label:
            full=label.split(' — ',1)[-1]
            self.mic_label.set('Micro sélectionné'); self.mic_detail.set(full)
        else:
            self.mic_label.set('Micro : aucun périphérique'); self.mic_detail.set('')

    def selected_microphone(self):
        label=self.mic_choice.get().strip()
        if label not in self.mic_devices:self.refresh_microphones(initial=True); label=self.mic_choice.get().strip()
        if label not in self.mic_devices:raise RuntimeError('Aucun microphone valide sélectionné.')
        return self.mic_devices[label]

    def on_microphone_changed(self):
        label=self.mic_choice.get().strip(); self.save_preferences(); self.update_mic_text(label)

    def install_hotkey(self):
        try:
            from pynput import keyboard
            def on_press(key):
                if key==keyboard.Key.f8:self.root.after(0,self.ask_voice)
            self.hotkey_listener=keyboard.Listener(on_press=on_press); self.hotkey_listener.daemon=True; self.hotkey_listener.start()
        except Exception as e:self.voice_state.set('F8 indisponible : utilise le bouton. '+str(e))

    def emit(self,sid,kind,*data):self.events.put((sid,kind,data))

    def capture_for_ai(self,max_size=(960,540),quality=74):
        with mss.mss() as cap:
            monitor=self.monitor.get()
            if monitor<1 or monitor>=len(cap.monitors):raise ValueError('Numéro d’écran inexistant.')
            shot=cap.grab(cap.monitors[monitor]); im=Image.frombytes('RGB',shot.size,shot.rgb); im.thumbnail(max_size)
            data=io.BytesIO(); im.save(data,format='JPEG',quality=quality,optimize=False)
            return base64.b64encode(data.getvalue()).decode()

    def prepare_voice(self):
        if self.voice_busy:return
        self.voice_busy=True; self.talk_button.state(['disabled']); sid=self.session_id
        def work():
            try:self.voice.prepare(lambda s:self.emit(sid,'voice_status',s)); self.emit(sid,'voice_ready')
            except Exception as e:self.emit(sid,'voice_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def ask_voice(self):
        if self.voice_busy or self.updating:return
        try:mic=self.selected_microphone()
        except Exception as e:messagebox.showerror('Microphone',str(e)); return
        self.voice_busy=True; self.talk_button.state(['disabled']); sid=self.session_id
        def work():
            try:
                q=self.voice.record_question(lambda s:self.emit(sid,'voice_status',s),lambda rms,peak,bands:self.emit(sid,'audio_level',rms,peak,bands),device=mic)
                if not q:self.emit(sid,'voice_error','Aucune phrase détectée. Vérifie le spectre et le micro choisi.'); return
                self.emit(sid,'question',q)
                hist='\n'.join(f'Joueur: {x}\nAcolyte: {y}' for x,y in self.history[-4:])
                ai=LocalAI(self.voice_stop); ai.verify(); image=None
                if needs_vision(q):
                    self.emit(sid,'voice_status','👁 Analyse de l’écran…')
                    image=self.capture_for_ai((960,540),76)
                else:
                    self.emit(sid,'voice_status','💬 Acolyte réfléchit…')
                started=time.monotonic(); a=ai.ask(image,q,hist,timeout=45); elapsed=time.monotonic()-started
                self.history.append((q,a)); self.history=self.history[-8:]
                self.emit(sid,'answer',q,a,elapsed,ai.last_scene,ai.last_observation,ai.used_vision)
                self.voice.speak(a,lambda s:self.emit(sid,'voice_status',s)); self.emit(sid,'voice_ready')
            except Exception as e:self.emit(sid,'voice_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def preview(self):
        try:
            with mss.mss() as cap:
                shot=cap.grab(cap.monitors[self.monitor.get()]); im=Image.frombytes('RGB',shot.size,shot.rgb)
            im.thumbnail((960,540)); w=tk.Toplevel(self.root); w.title('Aperçu local'); w.configure(bg=COLORS['bg'])
            photo=ImageTk.PhotoImage(im); label=tk.Label(w,image=photo,bg=COLORS['bg']); label.image=photo; label.pack(padx=10,pady=10)
        except Exception as e:messagebox.showerror('Capture',str(e))

    def start(self):
        if self.worker and self.worker.is_alive():return
        cfg={'monitor':self.monitor.get(),'interval':max(2.0,self.interval.get()),'expiry':self.expiry.get(),'minutes':self.minutes.get(),'onlygame':self.onlygame.get()}
        self.save_preferences(); self.session_id+=1; self.stop_event.clear(); self.start_button.state(['disabled'])
        self.worker=threading.Thread(target=self.run_auto,args=(cfg,self.session_id),daemon=True); self.worker.start()

    def run_auto(self,cfg,sid):
        client=LocalAI(self.stop_event); self.client=client
        try:
            client.verify(); deadline=time.monotonic()+cfg['minutes']*60; previous=''; count=advice_count=silent_count=0; gate=AdviceGate()
            self.emit(sid,'status','Analyse automatique active.')
            while not self.stop_event.is_set() and time.monotonic()<deadline:
                if cfg['onlygame'] and not active_game():self.stop_event.wait(.5); continue
                started=time.monotonic(); text=client.generate(self.capture_for_ai((640,360),62),previous,timeout=30); age=time.monotonic()-started; count+=1
                valid=usable(text,age,cfg['expiry'],previous) and gate.allow(client.last_category,time.monotonic())
                if valid:advice_count+=1; previous=text
                else:silent_count+=1
                self.emit(sid,'result',age,count,text,valid,client.last_category,advice_count,silent_count)
                self.stop_event.wait(max(0,cfg['interval']-(time.monotonic()-started)))
        except Exception as e:
            if not self.stop_event.is_set():self.emit(sid,'status','Arrêt : '+str(e))
        finally:self.client=None; self.emit(sid,'finished')

    def save_preferences(self):
        try:updater.write_json(APP_DIR/'settings.json',{k:v.get() for k,v in self.preferences.items()})
        except Exception:pass

    def configure_repo(self):
        repo=simpledialog.askstring('Dépôt GitHub','Lien du dépôt PUBLIC :',initialvalue=DEFAULT_REPO,parent=self.root)
        if repo:
            try:updater.write_json(APP_DIR/'update-config.json',{'repo':updater.normalize_repo(repo),'branch':'main'}); self.status.set('Dépôt GitHub configuré.')
            except Exception as e:messagebox.showerror('GitHub',str(e))

    def update_app(self):
        if getattr(self,"pc_busy",False):
            messagebox.showinfo("Mise à jour","Attends la fin de l’action PC avant de mettre à jour.");return
        if self.updating or self.voice_busy:return
        if self.worker and self.worker.is_alive():messagebox.showinfo('Mise à jour','Arrête d’abord l’analyse automatique.'); return
        config=updater.read_json(APP_DIR/'update-config.json',{}) or {'repo':DEFAULT_REPO,'branch':'main'}
        self.updating=True; self.update_button.state(['disabled']); self.status.set('Recherche d’une mise à jour…'); sid=self.session_id
        def work():
            try:
                change=updater.plan(APP_DIR,config['repo'],config.get('branch','main'))
                if change is None:self.emit(sid,'update_current'); return
                updater.apply(APP_DIR,change); self.emit(sid,'update_ok',change['version'])
            except Exception as e:self.emit(sid,'update_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def draw_audio(self,rms,peak,bands):
        level=min(1.0,max(0.0,rms*12.0)); active=int(round(level*20))
        for i,item in enumerate(self.audio_bars):
            height=3; fill='#26334f'
            if i<active:
                band=bands[min(len(bands)-1,int(i*len(bands)/20))] if bands else 0.0
                height=6+int(24*max(level,band*.75))
                if peak>=.98:fill=COLORS['danger']
                elif peak>=.85:fill=COLORS['warning']
                elif i>14:fill=COLORS['purple2']
                else:fill=COLORS['cyan']
            x1=6+i*23; x2=x1+15; self.meter.coords(item,x1,34-height,x2,32); self.meter.itemconfigure(item,fill=fill)
        if peak>=.98:self.mic_label.set('Micro : saturation')
        elif rms>.025:self.mic_label.set('Micro : voix détectée')
        elif rms>.004:self.mic_label.set('Micro : signal faible')
        else:self.mic_label.set('Micro : silence')

    def update_scene(self,scene,observation=''):
        scene=scene if scene in SCENE_LABELS else 'unknown'
        label=SCENE_LABELS[scene]; color=SCENE_COLORS[scene]
        self.scene_state.set(label); self.scene_badge.configure(bg=color,fg='#07101f' if scene in ('creative','match','menu') else COLORS['text'])
        self.context_big.set(label); self.context_label.configure(fg=color)
        if observation:self.context_observation.set(observation)
        else:self.context_observation.set('Contexte reconnu à partir de la capture actuelle.')

    def set_conversation_context(self,observation='Conversation sans vision.'):
        self.scene_state.set('CONVERSATION'); self.scene_badge.configure(bg=COLORS['purple'],fg=COLORS['text'])
        self.context_big.set('CONVERSATION'); self.context_label.configure(fg=COLORS['purple2'])
        self.context_observation.set(observation or 'Conversation sans vision.')

    def log_line(self,prefix,text,tag):
        self.log.insert('end','\n'+prefix+' ',tag); self.log.insert('end',text+'\n'); self.log.see('end')

    def poll(self):
        try:
            while True:
                sid,kind,data=self.events.get_nowait()
                if sid!=self.session_id and not kind.startswith('update_'):continue
                if kind=='audio_level':self.draw_audio(*data)
                elif kind=='voice_status':self.voice_state.set(data[0])
                elif kind=='voice_ready':
                    self.voice_busy=False; self.talk_button.state(['!disabled']); self.voice_state.set('Prêt. F8 = parler au coach.'); self.status.set('Acolyte prêt.')
                    self.update_mic_text(self.mic_choice.get().strip())
                elif kind=='voice_error':
                    self.voice_busy=False; self.talk_button.state(['!disabled']); self.voice_state.set('Erreur : '+data[0]); self.status.set('Erreur du mode vocal.')
                elif kind=='question':self.log_line('🎙 TOI >',data[0],'you')
                elif kind=='answer':
                    q,a,elapsed,scene,observation,used_vision=data
                    self.advice.set(a)
                    if used_vision:
                        self.metric.set(f'Vision : {elapsed*1000:.0f} ms'); self.status.set('Analyse visuelle terminée.')
                        self.update_scene(scene,observation)
                    else:
                        self.metric.set(f'Conversation : {elapsed*1000:.0f} ms'); self.status.set('Réponse conversationnelle.')
                        self.set_conversation_context(observation)
                    self.log_line('🤖 ACOLYTE >',a,'ai')
                elif kind=='status':self.status.set(data[0])
                elif kind=='finished':self.start_button.state(['!disabled']); self.status.set('Analyse auto terminée.')
                elif kind=='result':
                    age,count,text,valid,cat,advice_count,silent_count=data; self.metric.set(f'{age*1000:.0f} ms • auto {count} • conseils {advice_count} • silence {silent_count}')
                    if valid:
                        self.advice.set(text); self.log_line('⚡ AUTO >',text,'auto')
                        if self.speak.get() and not self.voice_busy:threading.Thread(target=self.voice.speak,args=(text,lambda s:self.emit(self.session_id,'voice_status',s)),daemon=True).start()
                elif kind.startswith('update_'):
                    self.updating=False
                    if kind=='update_ok':
                        self.restart_required=True; self.status.set('Version '+data[0]+' installée.'); messagebox.showinfo('Mise à jour','Version '+data[0]+' installée. Relance Acolyte Fortnite.')
                    else:
                        self.update_button.state(['!disabled']); self.status.set('Déjà à jour.' if kind=='update_current' else 'Échec : '+data[0])
        except queue.Empty:pass
        self.root.after(50,self.poll)

    def stop(self):
        self.stop_event.set(); self.status.set('Arrêt de l’analyse auto…')
        if self.client:threading.Thread(target=self.client.cancel,daemon=True).start()

    def close(self):
        if getattr(self,"pc_busy",False):
            messagebox.showinfo("Action en cours","Attends la fin de l’action PC avant de fermer Acolyte.");return
        self.save_preferences(); self.stop_event.set(); self.voice_stop.set()
        if self.hotkey_listener:
            try:self.hotkey_listener.stop()
            except Exception:pass
        try:sd.stop()
        except Exception:pass
        self.root.destroy()


if __name__=='__main__':
    if os.name!='nt':raise SystemExit('Cette version est destinée à Windows.')
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:pass
    root=tk.Tk(); Coach(root); root.mainloop()
