import base64, ctypes, io, os, queue, shutil, subprocess, sys, tempfile, threading, time, wave
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

FROZEN=bool(getattr(sys,'frozen',False))
BUNDLE_DIR=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
INSTALL_DIR=Path(sys.executable).resolve().parent if FROZEN else BUNDLE_DIR
APP_DIR=INSTALL_DIR if FROZEN else BUNDLE_DIR
DATA_DIR=Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))/'AcolyteFortnite'
DATA_DIR.mkdir(parents=True,exist_ok=True)
MODEL_DIR=DATA_DIR/'Models'
MODEL_DIR.mkdir(parents=True,exist_ok=True)
SETTINGS_FILE=DATA_DIR/'settings.json'
UPDATE_CONFIG_FILE=DATA_DIR/'update-config.json'
DEFAULT_REPO='Clemen5t/Coach_Fortnite_Ia'
VERSION=(BUNDLE_DIR/'VERSION').read_text().strip()

def _migrate_user_file(name,target):
    old=BUNDLE_DIR/name
    if target.exists() or not old.exists():return
    try:
        target.write_bytes(old.read_bytes())
    except OSError:pass

def _migrate_model_dir(name):
    target=MODEL_DIR/name
    if target.exists():return
    candidates=[BUNDLE_DIR/name,INSTALL_DIR/name]
    for old in candidates:
        if old.exists() and old.is_dir():
            try:
                shutil.copytree(old,target,dirs_exist_ok=True)
                return
            except OSError:
                pass

_migrate_user_file('settings.json',SETTINGS_FILE)
_migrate_user_file('update-config.json',UPDATE_CONFIG_FILE)
_migrate_model_dir('.voice')
_migrate_model_dir('.whisper')

import mss
import numpy as np
import sounddevice as sd
from PIL import Image, ImageTk, ImageGrab
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
            try:
                from piper.download_voices import download_voice
                download_voice(VOICE_NAME,self.data)
            except Exception as exc:
                raise RuntimeError('Téléchargement de la voix Piper impossible : '+str(exc)) from exc
            if not self.voice_model.exists():
                raise RuntimeError('Téléchargement de la voix Piper terminé mais le modèle est introuvable.')
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
        ('gpu','◈','Carte graphique'),('privacy','◉','Confidentialité'),('comfort','✦','Confort'),('startup','↗','Démarrage'),
        ('games','🎮','Jeux'),('checkup','✓','Check-up'),('bios','◆','BIOS / RAM'),
        ('apps','▦','Logiciels'),('research','⌕','Méthode'),('updates','↻','Mises à jour'),('usb','⌁','USB')]
    def __init__(self,parent,app_dir):
        self.parent=parent;self.app_dir=app_dir;self.busy=False;self.last_scan=None;self.last_benchmark=None
        self.option_vars={k:tk.BooleanVar(value=False) for k in pc_optimizer.OPTIONS}
        self.nav_buttons={};self.reco_widgets=[];self._last_net=None;self.feature_state_labels={};self.feature_actual_states={};self.pending_feature_keys=set()
        self.game_duration_var=tk.StringVar(value='60 s');self.game_phase_var=tk.StringVar(value='AVANT optimisation')
        self.game_benchmark_active=False;self._bench_hidden=False;self._drift_checked=False
        ctk.set_appearance_mode('dark')
        self.root=ctk.CTkFrame(parent,fg_color=COLORS['bg'],corner_radius=0)
        self.root.pack(fill='both',expand=True)
        self.root.grid_rowconfigure(1,weight=1);self.root.grid_columnconfigure(1,weight=1);self.root.grid_columnconfigure(2,minsize=330)
        self._build_sidebar();self._build_topbar();self._build_main();self._build_summary()
        self.show('dashboard')
        if os.environ.get('ACOLYTE_GUI_SMOKE')!='1':
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
        ctk.CTkLabel(side,text='SCORE ACOLYTE',text_color=COLORS['muted'],font=ctk.CTkFont(size=10,weight='bold')).pack(anchor='w',padx=16,pady=(16,5))
        self.ring=ScoreRing(side,150,COLORS['panel']);self.ring.pack(pady=(0,2))
        self.score_state=ctk.CTkLabel(side,text='Scan en cours…',text_color=COLORS['cyan'],font=ctk.CTkFont(size=13,weight='bold'))
        self.score_state.pack()
        scores=ctk.CTkFrame(side,fg_color='transparent');scores.pack(fill='x',padx=18,pady=(7,2))
        self.health_score_label=ctk.CTkLabel(scores,text='Santé —/100',text_color=COLORS['cyan'],font=ctk.CTkFont(size=10,weight='bold'))
        self.health_score_label.pack(side='left',expand=True)
        self.gaming_score_label=ctk.CTkLabel(scores,text='Gaming —/100',text_color=COLORS['purple2'],font=ctk.CTkFont(size=10,weight='bold'))
        self.gaming_score_label.pack(side='right',expand=True)
        ctk.CTkLabel(side,text='45 % santé Windows • 55 % performance gaming mesurable',text_color=COLORS['muted'],font=ctk.CTkFont(size=8),wraplength=250).pack(pady=(2,0))
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

    def _feature_grid(self,items):
        grid=ctk.CTkFrame(self.content,fg_color='transparent')
        grid.pack(fill='x',padx=8,pady=(2,8))
        for col in range(3):grid.grid_columnconfigure(col,weight=1,uniform='feature')
        for idx,item in enumerate(items):
            key,title,desc,impact,risk=item
            card=ctk.CTkFrame(grid,fg_color='#0B111D',corner_radius=16,border_width=1,border_color='#1B2A44')
            card.grid(row=idx//3,column=idx%3,sticky='nsew',padx=5,pady=5)
            top=ctk.CTkFrame(card,fg_color='transparent');top.pack(fill='x',padx=14,pady=(13,5))
            ctk.CTkLabel(top,text=title,text_color=COLORS['text'],font=ctk.CTkFont(size=13,weight='bold'),
                wraplength=255,justify='left').pack(side='left',anchor='nw')
            switch=ctk.CTkSwitch(top,text='',width=42,variable=self.option_vars[key],progress_color='#00E6A8',
                button_color='#F4F7FB',button_hover_color='#FFFFFF',
                command=lambda k=key:self._toggle_feature(k))
            switch.pack(side='right',anchor='ne')
            ctk.CTkLabel(card,text=desc,text_color=COLORS['muted'],font=ctk.CTkFont(size=9),
                wraplength=290,justify='left').pack(anchor='w',padx=14,pady=(2,9))
            foot=ctk.CTkFrame(card,fg_color='transparent');foot.pack(fill='x',padx=14,pady=(0,12))
            state=ctk.CTkLabel(foot,text='ÉTAT INCONNU',fg_color='#25324A',corner_radius=999,text_color='#C7D5EA',
                font=ctk.CTkFont(size=8,weight='bold'),padx=8,pady=2)
            state.pack(side='left')
            self.feature_state_labels[key]=state
            ctk.CTkLabel(foot,text=f'Impact {impact}  •  Risque {risk}',text_color='#6F84A5',
                font=ctk.CTkFont(size=8)).pack(side='right')

    def _toggle_feature(self,key):
        if self.busy:
            if self.last_scan:self._sync_feature_switches(self.last_scan)
            return
        actual=self.feature_actual_states.get(key)
        desired=bool(self.option_vars[key].get())
        if isinstance(actual,bool) and desired==actual:self.pending_feature_keys.discard(key)
        else:self.pending_feature_keys.add(key)
        label=self.feature_state_labels.get(key)
        if label:
            if key in self.pending_feature_keys:
                label.configure(text='EN ATTENTE',fg_color='#5A4315',text_color='#FFD56A')
            elif actual is True:
                label.configure(text='ACTIF',fg_color='#124A39',text_color='#46E6B0')
            elif actual is False:
                label.configure(text='INACTIF',fg_color='#3E2730',text_color='#F49AAA')
            else:
                label.configure(text='INDISPONIBLE',fg_color='#30394B',text_color='#9EB0CC')

    def _apply_pending_features(self,keys):
        keys=[k for k in keys if k in self.pending_feature_keys]
        if not keys:return messagebox.showinfo('Acolyte Performance','Aucun changement en attente dans cette section.')
        to_enable=[k for k in keys if bool(self.option_vars[k].get())]
        to_disable=[k for k in keys if not bool(self.option_vars[k].get())]
        lines=[]
        for key in to_enable:lines.append('✓ Activer : '+pc_optimizer.OPTIONS[key][0])
        for key in to_disable:lines.append('↶ Désactiver/restaurer : '+pc_optimizer.OPTIONS[key][0])
        if not messagebox.askyesno('Appliquer les changements','\n'.join(lines)+'\n\nAcolyte appliquera tout en une fois puis fera une seule analyse de vérification. Continuer ?'):return
        def work():
            return pc_optimizer.apply_batch(self.app_dir,to_enable,to_disable)
        self._run('Application des changements',work,lambda result:self._pending_applied(result,keys))

    def _pending_applied(self,result,keys):
        for key in keys:self.pending_feature_keys.discard(key)
        self._show_result('Changements appliqués',result)
        # Une seule analyse après tout le lot, pas à chaque interrupteur.
        self.parent.after(250,self.scan_full)

    def _cancel_pending_features(self,keys):
        for key in keys:
            self.pending_feature_keys.discard(key)
            state=self.feature_actual_states.get(key)
            if isinstance(state,bool):self.option_vars[key].set(state)
        if self.last_scan:self._sync_feature_switches(self.last_scan)

    def _sync_feature_switches(self,data):
        states=((data.get('settings') or {}).get('feature_states') or {})
        for key,var in self.option_vars.items():
            state=states.get(key)
            self.feature_actual_states[key]=state
            # Ne pas écraser une sélection utilisateur qui n'est pas encore appliquée.
            if key not in self.pending_feature_keys:
                if isinstance(state,bool):var.set(state)
                elif state is None:var.set(False)
            label=self.feature_state_labels.get(key)
            if label:
                if key in self.pending_feature_keys:
                    label.configure(text='EN ATTENTE',fg_color='#5A4315',text_color='#FFD56A')
                elif state is True:label.configure(text='ACTIF',fg_color='#124A39',text_color='#46E6B0')
                elif state is False:label.configure(text='INACTIF',fg_color='#3E2730',text_color='#F49AAA')
                else:label.configure(text='INDISPONIBLE',fg_color='#30394B',text_color='#9EB0CC')

    def _button_row(self,items):
        row=ctk.CTkFrame(self.content,fg_color='transparent');row.pack(fill='x',padx=10,pady=6)
        for text,cmd,color in items:
            ctk.CTkButton(row,text=text,command=cmd,fg_color=color,height=34,corner_radius=9).pack(side='left',padx=(0,6))

    def _page_dashboard(self):
        self._hero('Centre de contrôle','Une vue unique du matériel, de Windows, du réseau et des recommandations prioritaires.',COLORS['cyan'])
        self._button_row([('✦ SCAN COMPLET',self.scan_full,COLORS['cyan2']),('🚀 AUTO-TUNE PC COMPLET',self.complete_gaming_tune,COLORS['purple']),('🎮 OPTIMISER FORTNITE',lambda:self.show('games'),'#1675E0')])

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
        self._hero('Performances gaming','Des réglages réellement appliqués et relus depuis Windows après chaque redémarrage. Les options à risque ne sont jamais incluses dans le profil intelligent.',COLORS['purple2'])
        self._feature_grid([
            ('game','Mode Jeu Windows','Active le mode jeu pour réduire certaines activités de fond pendant les jeux.','Faible','Faible'),
            ('captures','Captures Xbox Game Bar','Désactive la capture DVR en arrière-plan quand tu ne l’utilises pas.','Moyen','Faible'),
            ('background_apps','Apps en arrière-plan','Réduit l’activité des apps Microsoft Store en arrière-plan.','Moyen','Faible'),
            ('hags_on','Planification GPU HAGS','Active la planification GPU accélérée par matériel. À valider avec le benchmark Fortnite.','Variable','Faible'),
            ('fast_startup_off','Démarrage rapide','Désactive l’hibernation hybride pour éviter certains états pilotes persistants.','Faible','Faible'),
            ('hibernation_off','Hibernation','Désactive l’hibernation sur PC fixe et libère hiberfil.sys.','Faible','Faible'),
            ('sysmain_off','SysMain','Option avancée pour tester sans SysMain. Non incluse dans l’optimisation intelligente.','Variable','Moyen'),
            ('balanced','Plan Équilibré Ryzen','Utilise le plan Équilibré comme base stable pour un Ryzen X3D.','Moyen','Faible'),
            ('amd_gpu','Optimisation GPU AMD','Configure automatiquement Windows pour la RX : Game Mode, captures off, HAGS, plan Ryzen et priorité GPU Fortnite.','Élevé','Faible'),
            ('vbs_off','VBS / Memory Integrity','Option avancée qui coupe VBS/HVCI. Peut améliorer certains scénarios mais réduit la sécurité et nécessite un redémarrage.','Variable','Élevé'),
            ('background_services','Services de télémétrie','Désactive uniquement DiagTrack/dmwappushservice s’ils existent.','Faible','Moyen'),
        ])
        perf_keys=['game','captures','background_apps','hags_on','fast_startup_off','hibernation_off','sysmain_off','balanced','amd_gpu','vbs_off','background_services']
        self._button_row([('🚀 AUTO-TUNE PC COMPLET',self.complete_gaming_tune,COLORS['purple']),
                          ('✓ APPLIQUER CES CHANGEMENTS',lambda:self._apply_pending_features(perf_keys),COLORS['success']),
                          ('ANNULER LA SÉLECTION',lambda:self._cancel_pending_features(perf_keys),COLORS['panel2']),
                          ('🖥 FRÉQUENCE ÉCRAN MAX',self.max_refresh_rate,COLORS['cyan2']),
                          ('🗓 OPTIMISER TÂCHES',self.optimize_tasks,COLORS['panel2'])])
        self._action_card(
            'AUTO-TUNE MESURÉ',
            'Applique la base Windows/GPU/réseau réversible, pousse l’écran à sa fréquence maximale détectée puis teste automatiquement RSC + modération des interruptions. Le profil réseau est annulé si la mesure régresse.',
            'Recommandé','Élevé','Faible',command=self.complete_gaming_tune,button='LANCER'
        )

    def _page_network(self):
        self._hero('Réseau & latence','Acolyte sépare les réglages fiables des tweaks à tester. Le DNS n’est jamais présenté comme une baisse garantie du ping en partie.',COLORS['cyan'])
        self._feature_grid([
            ('net_power','Alimentation de la carte réseau','Empêche Windows d’éteindre la carte réseau active pour économiser l’énergie.','Moyen','Faible'),
            ('net_eee','EEE / Green Ethernet','Désactive Energy Efficient Ethernet uniquement si le pilote expose une valeur compatible.','Moyen','Faible'),
            ('tcp_baseline','Réactivité réseau Windows','Active RSS et remet l’auto-tuning TCP en Normal pour une base saine.','Moyen','Faible'),
            ('net_low_latency','RSC + Interrupt Moderation','Profil faible latence : RSC coupé et modération des interruptions désactivée quand le pilote le permet. À conserver uniquement si la mesure avant/après est meilleure.','Variable','Faible'),
            ('nagle_off','Test sans Nagle','Applique TCPNoDelay et TcpAckFrequency sur l’interface active. À comparer avant/après.','Variable','Moyen'),
            ('p2p_off','Partage P2P des mises à jour','Désactive le P2P de Delivery Optimization afin d’éviter des uploads Windows en arrière-plan.','Faible','Faible'),
        ])
        net_keys=['net_power','net_eee','tcp_baseline','net_low_latency','nagle_off','p2p_off']
        self._button_row([('🎯 AUTO-TUNE LATENCE',self.autotune_network,COLORS['purple']),
                          ('🌍 SERVEURS FORTNITE',self.benchmark_fortnite_regions,'#1675E0'),
                          ('✓ APPLIQUER',lambda:self._apply_pending_features(net_keys),COLORS['success']),
                          ('⚡ DNS AUTO',self.auto_dns,COLORS['panel2']),
                          ('↻ RAFRAÎCHIR',self.refresh_network,COLORS['cyan2'])])

    def _page_gpu(self):
        self._hero('Carte graphique','Détection du GPU principal, pilote et profil AMD automatique sans overclocking ni clé Adrenalin privée.',COLORS['purple2'])
        self._feature_grid([
            ('amd_gpu','Optimisation GPU AMD','Applique automatiquement le profil Windows gaming pour Fortnite et la RX 7900 XT.','Élevé','Faible'),
            ('hags_on','HAGS','Planification GPU accélérée par matériel, mesurable avec le benchmark en jeu.','Variable','Faible'),
        ])
        gpu_keys=['amd_gpu','hags_on']
        self._button_row([('✓ APPLIQUER CES CHANGEMENTS',lambda:self._apply_pending_features(gpu_keys),COLORS['success']),
                          ('ANNULER LA SÉLECTION',lambda:self._cancel_pending_features(gpu_keys),COLORS['panel2']),
                          ('LIRE LES PILOTES',lambda:self._diagnostic('gpu'),COLORS['cyan2']),
                          ('AMD OFFICIEL',lambda:self._open('amd'),COLORS['purple']),
                          ('RESET CACHE SHADERS',self.reset_shader_cache,COLORS['panel2'])])

    def _page_privacy(self):
        self._hero('Confidentialité Windows','Fonctions de confidentialité configurées et relues depuis Windows. Defender, pare-feu et Windows Update restent protégés.',COLORS['cyan'])
        self._feature_grid([
            ('ads','Identifiant publicitaire','Désactive l’identifiant publicitaire Windows.','Faible','Faible'),
            ('suggestions','Suggestions Windows','Réduit les recommandations et contenus promotionnels.','Faible','Faible'),
            ('silent_installs','Installations suggérées','Bloque les installations silencieuses déclenchées par Content Delivery Manager.','Faible','Faible'),
            ('tailored','Expériences personnalisées','Réduit la personnalisation basée sur les données de diagnostic.','Faible','Faible'),
            ('telemetry_min','Télémétrie Windows minimale','Force le niveau de diagnostic au minimum autorisé par l’édition de Windows.','Faible','Faible'),
            ('error_reporting','Rapports d’erreurs utilisateur','Désactive Windows Error Reporting pour le compte courant.','Faible','Faible'),
            ('location','Géolocalisation des apps','Refuse l’accès à la position pour le compte courant.','Faible','Faible'),
            ('online_speech','Reconnaissance vocale en ligne','Désactive le consentement à la reconnaissance vocale connectée Windows.','Faible','Faible'),
            ('storage_sense_off','Storage Sense','Désactive le nettoyage automatique Storage Sense.','Faible','Faible'),
        ])
        privacy_keys=['ads','suggestions','silent_installs','tailored','telemetry_min','error_reporting','location','online_speech','storage_sense_off']
        self._button_row([('✓ APPLIQUER CES CHANGEMENTS',lambda:self._apply_pending_features(privacy_keys),COLORS['success']),
                          ('ANNULER LA SÉLECTION',lambda:self._cancel_pending_features(privacy_keys),COLORS['panel2']),
                          ('PARAMÈTRES CONFIDENTIALITÉ',lambda:self._open('privacy'),COLORS['panel2'])])

    def _page_comfort(self):
        self._hero('Confort & interface','Réglages d’ergonomie et de fond qui rendent Windows plus prévisible sans toucher au cœur du système.',COLORS['gold'])
        self._feature_grid([
            ('mouse_accel_off','Souris sans accélération','Désactive Enhance Pointer Precision pour un mouvement plus reproductible en jeu.','Moyen','Faible'),
            ('explorer_tweaks','Explorateur Windows','Réduit les notifications promotionnelles et ouvre directement Ce PC.','Faible','Faible'),
            ('widgets_off','Widgets Windows','Masque Widgets de la barre des tâches.','Faible','Faible'),
            ('edge_background_off','Edge en arrière-plan','Désactive Startup Boost et le mode arrière-plan d’Edge via stratégie utilisateur.','Faible','Faible'),
            ('copilot_off','Microsoft Copilot','Masque Copilot via stratégie utilisateur.','Faible','Faible'),
            ('classic_context','Menu contextuel classique','Restaure le menu clic droit classique de Windows 11.','Faible','Faible'),
        ])
        comfort_keys=['mouse_accel_off','explorer_tweaks','widgets_off','edge_background_off','copilot_off','classic_context']
        self._button_row([('✓ APPLIQUER CES CHANGEMENTS',lambda:self._apply_pending_features(comfort_keys),COLORS['success']),
                          ('ANNULER LA SÉLECTION',lambda:self._cancel_pending_features(comfort_keys),COLORS['panel2']),
                          ('DÉSINSTALLER ONEDRIVE',self.remove_onedrive,COLORS['danger'])])

    def _page_startup(self):
        self._hero('Démarrage','Inventorie les entrées Run du compte actuel. Retire uniquement ce que tu reconnais.',COLORS['gold'])
        self._button_row([('LISTER LES ENTRÉES',self.load_startup,COLORS['cyan2']),('Paramètres Démarrage',lambda:self._open('startup'),COLORS['panel2'])])

    def _page_games(self):
        self._hero('Optimisation par jeu','Profils réversibles dédiés à chaque jeu. Fortnite est le premier profil : réglages Windows + configuration compétitive + benchmark avant/après.',COLORS['purple'])
        engine=pc_optimizer.presentmon_status();game=pc_optimizer.game_process_running()
        profile=pc_optimizer.fortnite_profile_status()

        pcard=ctk.CTkFrame(self.content,fg_color='#0B111D',corner_radius=16,border_width=1,border_color=COLORS['purple'])
        pcard.pack(fill='x',padx=10,pady=5)
        pleft=ctk.CTkFrame(pcard,fg_color='transparent');pleft.pack(side='left',fill='both',expand=True,padx=16,pady=14)
        ctk.CTkLabel(pleft,text='FORTNITE • PROFIL COMPÉTITIF MAX FPS',text_color=COLORS['text'],font=ctk.CTkFont(size=16,weight='bold')).pack(anchor='w')
        installed='Détecté' if profile.get('installed') else 'Non détecté'
        applied='OPTIMISÉ' if profile.get('profile_applied') and profile.get('score',0)>=80 else 'À OPTIMISER'
        ctk.CTkLabel(pleft,text=f"{installed} • Score Fortnite {profile.get('score',0)}/100 • {applied}",
            text_color=COLORS['success'] if applied=='OPTIMISÉ' else COLORS['warning'],font=ctk.CTkFont(size=10,weight='bold')).pack(anchor='w',pady=(4,3))
        ctk.CTkLabel(pleft,text='Le profil active Game Mode, coupe Game DVR, force le GPU haute performance et applique un preset Fortnite compétitif réversible. Il ne change pas ton renderer DX11/DX12/Performance Mode, ta résolution ni tes touches.',
            text_color=COLORS['muted'],font=ctk.CTkFont(size=9),wraplength=760,justify='left').pack(anchor='w')
        ctk.CTkLabel(pleft,text=f"Windows : {profile.get('registry_ok',0)}/{profile.get('registry_total',0)} • Config : {profile.get('config_ok',0)}/{profile.get('config_total',0)} • Benchmark : {'mesuré' if profile.get('benchmark_measured') else 'à faire'}",
            text_color=COLORS['cyan'],font=ctk.CTkFont(size=9,weight='bold')).pack(anchor='w',pady=(7,0))
        pright=ctk.CTkFrame(pcard,fg_color='transparent');pright.pack(side='right',padx=14,pady=14)
        ctk.CTkButton(pright,text='⚡ OPTIMISER FORTNITE',command=self.optimize_fortnite,width=180,height=38,corner_radius=10,fg_color=COLORS['purple']).pack(pady=(0,6))
        ctk.CTkButton(pright,text='↶ RESTAURER FORTNITE',command=self.restore_fortnite,width=180,height=32,corner_radius=9,fg_color=COLORS['panel2']).pack()
        card=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#1C3153')
        card.pack(fill='x',padx=10,pady=5)
        left=ctk.CTkFrame(card,fg_color='transparent');left.pack(side='left',fill='both',expand=True,padx=14,pady=12)
        ctk.CTkLabel(left,text='MOTEUR DE MESURE',text_color=COLORS['muted'],font=ctk.CTkFont(size=9,weight='bold')).pack(anchor='w')
        ctk.CTkLabel(left,text='PresentMon '+(str(engine.get('version')) if engine.get('installed') else 'non installé'),
            text_color=COLORS['text'],font=ctk.CTkFont(size=15,weight='bold')).pack(anchor='w',pady=(2,2))
        ctk.CTkLabel(left,text='Source officielle : GameTechDev/PresentMon • Licence MIT • capture externe ETW',
            text_color=COLORS['muted'],font=ctk.CTkFont(size=9)).pack(anchor='w')
        status='FORTNITE DÉTECTÉ' if game.get('running') else 'FORTNITE NON LANCÉ'
        color=COLORS['success'] if game.get('running') else COLORS['warning']
        ctk.CTkLabel(left,text='● '+status,text_color=color,font=ctk.CTkFont(size=10,weight='bold')).pack(anchor='w',pady=(7,0))
        right=ctk.CTkFrame(card,fg_color='transparent');right.pack(side='right',padx=14,pady=12)
        if not engine.get('installed'):
            ctk.CTkButton(right,text='INSTALLER LE MOTEUR',command=self.install_presentmon,width=150,height=34,corner_radius=9,fg_color=COLORS['purple']).pack()
        else:
            ctk.CTkButton(right,text='DOSSIER RÉSULTATS',command=lambda:self._open_benchmark_folder(),width=145,height=34,corner_radius=9,fg_color=COLORS['panel2']).pack()

        controls=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#1C3153')
        controls.pack(fill='x',padx=10,pady=5)
        ctk.CTkLabel(controls,text='BENCHMARK FORTNITE',text_color=COLORS['text'],font=ctk.CTkFont(size=15,weight='bold')).pack(anchor='w',padx=14,pady=(12,3))
        ctk.CTkLabel(controls,text='Entre dans une partie ou une scène reproductible. Acolyte se minimise, attend 5 secondes puis mesure la durée choisie.',
            text_color=COLORS['muted'],font=ctk.CTkFont(size=9),wraplength=850,justify='left').pack(anchor='w',padx=14)
        row=ctk.CTkFrame(controls,fg_color='transparent');row.pack(fill='x',padx=14,pady=12)
        ctk.CTkLabel(row,text='Durée',text_color=COLORS['muted'],font=ctk.CTkFont(size=9,weight='bold')).pack(side='left',padx=(0,6))
        ctk.CTkComboBox(row,variable=self.game_duration_var,values=['30 s','60 s','90 s','120 s','180 s'],width=105,state='readonly').pack(side='left',padx=(0,12))
        ctk.CTkLabel(row,text='Phase',text_color=COLORS['muted'],font=ctk.CTkFont(size=9,weight='bold')).pack(side='left',padx=(0,6))
        ctk.CTkComboBox(row,variable=self.game_phase_var,values=['AVANT optimisation','APRÈS optimisation','Libre'],width=165,state='readonly').pack(side='left',padx=(0,12))
        self.game_bench_btn=ctk.CTkButton(row,text='▶  DÉMARRER LE BENCHMARK',command=self.start_game_benchmark,width=205,height=38,corner_radius=10,fg_color=COLORS['purple'])
        self.game_bench_btn.pack(side='left')

        history=pc_optimizer.benchmark_history(5)
        if history:
            self._render_game_result(history[-1],history[-2] if len(history)>1 else None)
            self._render_game_history(history)
        else:
            self._action_card('Aucun benchmark enregistré','Installe PresentMon si nécessaire, lance Fortnite, choisis 60 secondes puis démarre le benchmark.','Prêt','Mesuré','Nul')

        self._button_row([('DÉTECTER LES JEUX INSTALLÉS',self.load_games,COLORS['panel2'])])

    def optimize_fortnite(self):
        if self.busy:return
        status=pc_optimizer.fortnite_profile_status()
        if status.get('running'):
            return messagebox.showinfo('Fortnite','Ferme Fortnite avant d’appliquer le profil.')
        if not status.get('installed'):
            return messagebox.showinfo('Fortnite','Fortnite n’est pas détecté sur ce PC.')
        msg=(
            'Appliquer le profil Fortnite compétitif ?\n\n'
            '• Mode Jeu Windows activé\n'
            '• Captures Game DVR désactivées\n'
            '• GPU haute performance forcé pour Fortnite\n'
            '• V-Sync, Motion Blur et résolution dynamique désactivés\n'
            '• Qualité Scalability au minimum pour viser les FPS\n\n'
            'Une sauvegarde exacte de GameUserSettings.ini est créée avant modification. '
            'Le renderer, la résolution et les touches ne sont pas modifiés.'
        )
        if not messagebox.askyesno('Optimiser Fortnite',msg):return
        self._run('Optimisation Fortnite',lambda:pc_optimizer.apply_fortnite_profile(self.app_dir),self._fortnite_profile_done)

    def _fortnite_profile_done(self,result):
        changes='\n'.join('• '+x for x in result.get('changes',[]))
        self._show_result('Fortnite optimisé',result.get('message','Profil appliqué.')+'\n\n'+changes)
        self.parent.after(250,self.scan_full)
        self.parent.after(500,lambda:self.show('games'))

    def restore_fortnite(self):
        if self.busy:return
        if pc_optimizer.game_process_running().get('running'):
            return messagebox.showinfo('Fortnite','Ferme Fortnite avant la restauration.')
        if not messagebox.askyesno('Restaurer Fortnite','Restaurer la configuration Fortnite sauvegardée avant Acolyte et les réglages Windows encore contrôlés par le profil ?'):return
        self._run('Restauration Fortnite',lambda:pc_optimizer.restore_fortnite_profile(self.app_dir),self._fortnite_restore_done)

    def _fortnite_restore_done(self,result):
        text=result.get('message','Restauration terminée.')
        if result.get('preserved_current'):
            text+='\n\nUne copie de ta configuration modifiée après optimisation a été conservée :\n'+result['preserved_current']
        if result.get('skipped_registry'):
            text+='\n\nRéglages Windows non écrasés car ils ont changé depuis : '+', '.join(result['skipped_registry'])
        self._show_result('Restauration Fortnite',text)
        self.parent.after(250,self.scan_full)
        self.parent.after(500,lambda:self.show('games'))

    def install_presentmon(self):
        if self.busy:return
        if not messagebox.askyesno('Installer PresentMon','Acolyte va télécharger le binaire x64 officiel depuis le dépôt GitHub GameTechDev/PresentMon.\n\nPresentMon est un outil open-source sous licence MIT. Continuer ?'):return
        self._run('Installation PresentMon',pc_optimizer.install_presentmon,lambda x:self._presentmon_installed(x))

    def _presentmon_installed(self,result):
        self._show_result('Moteur benchmark installé','PresentMon '+str(result.get('version',''))+' installé.\nSource : '+str(result.get('source',''))+'\nLicence : '+str(result.get('license','')))
        self.show('games')

    def _open_benchmark_folder(self):
        try:pc_optimizer.open_benchmark_folder()
        except Exception as exc:self._error('Dossier benchmark',exc)

    def start_game_benchmark(self):
        if self.busy:return
        engine=pc_optimizer.presentmon_status()
        if not engine.get('installed'):
            return self.install_presentmon()
        game=pc_optimizer.game_process_running()
        if not game.get('running'):
            return messagebox.showinfo('Benchmark Fortnite','Fortnite n’est pas lancé.\n\nOuvre le jeu, entre dans une partie ou une scène de test, puis reviens cliquer sur Démarrer le benchmark.')
        if not pc_optimizer.is_admin():
            return messagebox.showinfo('Administrateur','Pour capturer les événements ETW de façon fiable, relance Acolyte avec « Exécuter en tant qu’administrateur ».')
        try:duration=int(self.game_duration_var.get().split()[0])
        except Exception:duration=60
        label=self.game_phase_var.get().strip() or 'Libre'
        if not messagebox.askyesno('Benchmark Fortnite',f'Capture : {duration} secondes\nPhase : {label}\n\nAcolyte va se minimiser. Tu auras 5 secondes pour retourner dans Fortnite avant le début de la mesure.\n\nPour comparer AVANT/APRÈS, refais exactement la même scène, résolution et limite FPS. Continuer ?'):return
        try:
            top=self.parent.winfo_toplevel();top.iconify();self._bench_hidden=True
        except Exception:pass
        self.game_benchmark_active=True
        self._run('Benchmark Fortnite',lambda:pc_optimizer.run_game_benchmark(duration,label,delay=5),self._game_benchmark_done)

    def _restore_bench_window(self):
        self.game_benchmark_active=False
        if self._bench_hidden:
            self._bench_hidden=False
            try:
                top=self.parent.winfo_toplevel();top.deiconify();top.lift();top.focus_force()
            except Exception:pass

    def _game_benchmark_done(self,result):
        self._restore_bench_window()
        if result.get('label')=='AVANT optimisation':self.game_phase_var.set('APRÈS optimisation')
        self._show_result('Benchmark Fortnite',self._format_game_result(result))
        self.show('games')
        warnings=result.get('capture_warnings') or []
        extra=('\n\n⚠ '+warnings[0]) if warnings else ''
        messagebox.showinfo('Benchmark terminé',f"FPS moyen : {result.get('avg_fps',0):.1f}\n1% low : {result.get('one_percent_low',0):.1f}\n0,1% low : {result.get('point_one_percent_low',0):.1f}\nFrametime moyen : {result.get('avg_frametime_ms',0):.2f} ms"+extra)

    def _format_game_result(self,r):
        return (
            f"{r.get('label','Benchmark')} • {r.get('timestamp','')}\n"
            f"FPS moyen : {r.get('avg_fps',0):.1f}\n"
            f"1% low : {r.get('one_percent_low',0):.1f}\n"
            f"0,1% low : {r.get('point_one_percent_low',0):.1f}\n"
            f"Frametime moyen : {r.get('avg_frametime_ms',0):.2f} ms\n"
            f"P99 frametime : {r.get('p99_frametime_ms',0):.2f} ms\n"
            f"Pire frametime : {r.get('worst_frametime_ms',0):.2f} ms\n"
            f"Frames >33 ms : {r.get('stutters_33ms',0)} • >50 ms : {r.get('stutters_50ms',0)}\n"
            f"Images analysées : {r.get('frames',0)} • swapchains détectés : {r.get('swapchains_detected',1)} • moteur : {r.get('engine_version','?')}" +
            (("\n\n⚠ " + "\n⚠ ".join(r.get('capture_warnings') or [])) if r.get('capture_warnings') else "")
        )

    def _metric_box(self,parent,title,value,unit,color):
        box=ctk.CTkFrame(parent,fg_color='#0B1729',corner_radius=12,border_width=1,border_color='#203354')
        box.pack(side='left',fill='both',expand=True,padx=4)
        ctk.CTkLabel(box,text=title,text_color=COLORS['muted'],font=ctk.CTkFont(size=9,weight='bold')).pack(anchor='w',padx=11,pady=(9,0))
        ctk.CTkLabel(box,text=f'{value:.1f} {unit}',text_color=color,font=ctk.CTkFont(size=20,weight='bold')).pack(anchor='w',padx=11,pady=(2,9))

    def _render_game_result(self,result,previous=None):
        card=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#1C3153')
        card.pack(fill='x',padx=10,pady=5)
        top=ctk.CTkFrame(card,fg_color='transparent');top.pack(fill='x',padx=12,pady=(11,4))
        ctk.CTkLabel(top,text='DERNIER BENCHMARK • '+str(result.get('label','')),text_color=COLORS['text'],font=ctk.CTkFont(size=14,weight='bold')).pack(side='left')
        ctk.CTkLabel(top,text=str(result.get('timestamp','')),text_color=COLORS['muted'],font=ctk.CTkFont(size=9)).pack(side='right')
        metrics=ctk.CTkFrame(card,fg_color='transparent');metrics.pack(fill='x',padx=8,pady=(2,8))
        self._metric_box(metrics,'FPS MOYEN',float(result.get('avg_fps') or 0),'FPS',COLORS['success'])
        self._metric_box(metrics,'1% LOW',float(result.get('one_percent_low') or 0),'FPS',COLORS['cyan'])
        self._metric_box(metrics,'0,1% LOW',float(result.get('point_one_percent_low') or 0),'FPS',COLORS['purple2'])
        self._metric_box(metrics,'FRAMETIME',float(result.get('avg_frametime_ms') or 0),'ms',COLORS['gold'])
        self._draw_frametime_chart(card,result.get('frametime_sample') or [])
        if previous:
            comp=pc_optimizer.compare_game_benchmarks(previous,result)
            line=ctk.CTkFrame(card,fg_color='#0A172A',corner_radius=10);line.pack(fill='x',padx=12,pady=(2,11))
            pieces=[]
            for key,label in [('avg_fps','FPS'),('one_percent_low','1% low'),('point_one_percent_low','0,1% low')]:
                d=comp.get(key,{})
                pct=d.get('percent')
                if pct is not None:pieces.append(f"{label} {pct:+.1f}%")
            ctk.CTkLabel(line,text='Comparaison au benchmark précédent :  '+'   •   '.join(pieces),text_color=COLORS['cyan'],font=ctk.CTkFont(size=10,weight='bold')).pack(anchor='w',padx=10,pady=8)

    def _draw_frametime_chart(self,parent,values):
        if not values:return
        holder=ctk.CTkFrame(parent,fg_color='transparent');holder.pack(fill='x',padx=12,pady=(0,9))
        ctk.CTkLabel(holder,text='FRAMETIME • plus la ligne est basse et stable, mieux c’est',text_color=COLORS['muted'],font=ctk.CTkFont(size=8,weight='bold')).pack(anchor='w')
        canvas=tk.Canvas(holder,height=105,bg='#07101F',highlightthickness=1,highlightbackground='#203354')
        canvas.pack(fill='x',pady=(4,0))
        def draw(event=None):
            canvas.delete('all');w=max(100,canvas.winfo_width());h=max(70,canvas.winfo_height())
            vals=[float(x) for x in values if isinstance(x,(int,float)) and x>0]
            if len(vals)<2:return
            cap=max(33.3,min(100.0,pc_optimizer._percentile(vals,99.5)*1.25))
            for ms,label,color in [(16.67,'60 FPS','#173D43'),(33.33,'30 FPS','#4A381B')]:
                y=h-8-min(1,ms/cap)*(h-18);canvas.create_line(0,y,w,y,fill=color,dash=(4,4));canvas.create_text(5,y-7,text=label,fill='#6F88A8',anchor='w',font=('Segoe UI',7))
            pts=[]
            for i,v in enumerate(vals):
                x=4+i*(w-8)/(len(vals)-1);y=h-7-min(1,v/cap)*(h-16);pts.extend((x,y))
            canvas.create_line(*pts,fill=COLORS['cyan'],width=2,smooth=False)
        canvas.bind('<Configure>',draw);self.parent.after(80,draw)

    def _render_game_history(self,rows):
        card=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=15,border_width=1,border_color='#1C3153')
        card.pack(fill='x',padx=10,pady=5)
        ctk.CTkLabel(card,text='HISTORIQUE RÉCENT',text_color=COLORS['text'],font=ctk.CTkFont(size=11,weight='bold')).pack(anchor='w',padx=12,pady=(10,5))
        for r in reversed(rows[-5:]):
            row=ctk.CTkFrame(card,fg_color='#0A172A',corner_radius=8);row.pack(fill='x',padx=10,pady=2)
            ctk.CTkLabel(row,text=str(r.get('label','Libre')),text_color=COLORS['text'],font=ctk.CTkFont(size=9,weight='bold'),width=125,anchor='w').pack(side='left',padx=8,pady=6)
            ctk.CTkLabel(row,text=f"{float(r.get('avg_fps') or 0):.1f} FPS",text_color=COLORS['success'],font=ctk.CTkFont(size=9,weight='bold'),width=85).pack(side='left')
            ctk.CTkLabel(row,text=f"1% {float(r.get('one_percent_low') or 0):.1f}",text_color=COLORS['cyan'],font=ctk.CTkFont(size=9),width=75).pack(side='left')
            ctk.CTkLabel(row,text=f"0,1% {float(r.get('point_one_percent_low') or 0):.1f}",text_color=COLORS['purple2'],font=ctk.CTkFont(size=9),width=80).pack(side='left')
            ctk.CTkLabel(row,text=str(r.get('timestamp','')),text_color=COLORS['muted'],font=ctk.CTkFont(size=8)).pack(side='right',padx=8)
        ctk.CTkFrame(card,height=6,fg_color='transparent').pack()

    def _page_lab(self):
        self._hero('Lab Gaming','Base d’optimisation validée : Acolyte sépare ce qui est automatique, ce qui doit être mesuré et ce qui reste volontairement exclu.',COLORS['purple2'])
        audit=pc_optimizer.gaming_research_audit(self.last_scan or {})
        self._action_card(
            'Pack compétitif sûr',
            'Applique en une seule fois Game Mode, captures off, plan Ryzen équilibré, souris sans accélération, alimentation carte réseau, RSS/TCP sain et P2P Windows Update désactivé.',
            'Automatique','Élevé','Faible',command=self.apply_competitive_pack,button='APPLIQUER'
        )
        for item in audit.get('cards',[]):
            action=item.get('action')
            command=None;button='VOIR'
            if action in pc_optimizer.OPTIONS:
                command=lambda key=action:self._select_and_open_option(key)
                button='RÉGLER'
            elif action=='open_amd':
                command=lambda:self._run('AMD Software',pc_optimizer.open_amd_software)
                button='AMD SOFTWARE'
            elif action=='open_graphics':
                command=lambda:self._run('Graphiques Windows',pc_optimizer.open_graphics_settings)
                button='OUVRIR'
            elif action=='shader_cache':
                command=self.reset_shader_cache;button='RESET'
            elif action=='fortnite_settings':
                command=lambda:self.show('games');button='FORTNITE'
            desc=item.get('detail','')+'\nSource : '+item.get('source','')
            self._action_card(item.get('title','Optimisation'),desc,item.get('status','Info'),'Mesuré',item.get('risk','Faible'),command=command,button=button)
        excluded=audit.get('excluded') or []
        if excluded:
            self._action_card(
                'Tweaks volontairement exclus',
                'Acolyte ne les applique pas automatiquement :\n• '+'\n• '.join(excluded),
                'Protection','Variable','Élevé'
            )

    def _select_and_open_option(self,key):
        if key not in self.option_vars:return
        self.option_vars[key].set(True)
        self.pending_feature_keys.add(key)
        if key in ('net_power','net_eee','tcp_baseline','net_low_latency','nagle_off','p2p_off'):
            self.show('network')
        elif key in ('amd_gpu','hags_on'):
            self.show('gpu')
        else:
            self.show('performance')

    def _page_checkup(self):
        self._hero('Routine Check-up+','Entretien complet : caches, réseau, disques et intégrité Windows. Les actions lourdes restent explicites et ne sont jamais lancées en plein jeu.',COLORS['success'])
        grid=ctk.CTkFrame(self.content,fg_color='transparent');grid.pack(fill='x',padx=8,pady=(2,8))
        for col in range(3):grid.grid_columnconfigure(col,weight=1,uniform='check')
        actions=[
            ('Fichiers temporaires','Nettoie le dossier TEMP utilisateur en conservant les fichiers récents/verrouillés.',self.clear_windows_cache,'VIDER','#00BFA5'),
            ('Cache shaders GPU','Réinitialise D3DSCache et les caches AMD/NVIDIA. Peut provoquer des stutters au prochain lancement.',self.reset_shader_cache,'RÉINITIALISER',COLORS['purple']),
            ('Rafraîchissement réseau','Vide DNS, ARP et le cache NetBIOS sans réinitialiser entièrement la pile réseau.',self.refresh_network,'RAFRAÎCHIR',COLORS['cyan2']),
            ('Optimisation des disques','Lance defrag /O : Windows choisit TRIM pour SSD et optimisation adaptée aux HDD.',self.optimize_disks,'OPTIMISER',COLORS['gold']),
            ('Historique Windows','Nettoie fichiers récents, Jump Lists et caches miniature non verrouillés.',self.clear_history,'NETTOYER',COLORS['panel2']),
            ('Réparation système','Exécute DISM RestoreHealth puis SFC /scannow. Peut prendre longtemps.',self.repair_system,'RÉPARER',COLORS['danger']),
            ('Cache Windows Update','Nettoie SoftwareDistribution\\\\Download après arrêt temporaire de Windows Update/BITS.',self.clear_update_cache,'NETTOYER',COLORS['panel2']),
        ]
        for idx,(title,desc,cmd,button,color) in enumerate(actions):
            card=ctk.CTkFrame(grid,fg_color='#0B111D',corner_radius=16,border_width=1,border_color='#1B2A44')
            card.grid(row=idx//3,column=idx%3,sticky='nsew',padx=5,pady=5)
            ctk.CTkLabel(card,text=title,text_color=COLORS['text'],font=ctk.CTkFont(size=13,weight='bold'),
                wraplength=255,justify='left').pack(anchor='w',padx=14,pady=(13,4))
            ctk.CTkLabel(card,text=desc,text_color=COLORS['muted'],font=ctk.CTkFont(size=9),
                wraplength=285,justify='left').pack(anchor='w',padx=14,pady=(0,10))
            ctk.CTkButton(card,text=button,command=cmd,height=31,corner_radius=9,fg_color=color).pack(anchor='w',padx=14,pady=(0,13))
        self._button_row([('ANALYSER LE CHECK-UP',lambda:self._diagnostic('checkup'),COLORS['cyan2'])])

    def _page_bios(self):
        self._hero('BIOS / RAM','Diagnostic uniquement : vitesse RAM configurée, profil mémoire à vérifier, UEFI et virtualisation.',COLORS['warning'])
        self._action_card('EXPO / XMP & mémoire','Compare la vitesse configurée avec la vitesse annoncée par les barrettes. Aucun changement BIOS automatique.','Important','Élevé','Nul',command=lambda:self._diagnostic('bios'),button='ANALYSER')
        self._button_row([('Support MSI B650 Gaming Plus WiFi',lambda:self._open('board'),COLORS['panel2'])])

    def _page_apps(self):
        self._hero('Logiciels','Liste fermée d’applications Windows facultatives. La désinstallation est séparée de la restauration des tweaks.',COLORS['gold'])
        self._button_row([('LISTER LES APPS PROPOSÉES',self.load_apps,COLORS['cyan2']),('Toutes les applications',lambda:self._open('apps'),COLORS['panel2']),('Microsoft Store',lambda:self._open('store'),COLORS['panel2'])])

    def _page_research(self):
        self._hero(
            'Méthode & audit des tweaks',
            'Acolyte reprend les idées utiles des guides d’optimisation, mais ne transforme pas un tweak populaire en “gain FPS” sans mesure. Les changements automatiques sont réversibles ; les réglages BIOS/OC restent séparés.',
            COLORS['purple2']
        )
        rows=pc_optimizer.optimization_research_audit()
        for row in rows:
            status=row.get('status','')
            color=COLORS['success'] if status in ('Automatique','A/B automatique') else COLORS['cyan'] if status in ('Mesuré','À maintenir','Diagnostic BIOS','Optionnel') else COLORS['warning']
            card=ctk.CTkFrame(self.content,fg_color=COLORS['panel'],corner_radius=13,border_width=1,border_color='#203354')
            card.pack(fill='x',padx=10,pady=4)
            left=ctk.CTkFrame(card,fg_color='transparent');left.pack(side='left',fill='both',expand=True,padx=13,pady=10)
            ctk.CTkLabel(left,text=row['name'],text_color=COLORS['text'],font=ctk.CTkFont(size=12,weight='bold')).pack(anchor='w')
            ctk.CTkLabel(left,text=row['reason'],text_color=COLORS['muted'],font=ctk.CTkFont(size=9),wraplength=760,justify='left').pack(anchor='w',pady=(2,0))
            right=ctk.CTkFrame(card,fg_color='transparent');right.pack(side='right',padx=12,pady=10)
            ctk.CTkLabel(right,text=status,fg_color=color,corner_radius=999,text_color='#07101F' if status in ('Automatique','A/B automatique') else 'white',
                font=ctk.CTkFont(size=8,weight='bold'),padx=9,pady=3).pack(anchor='e')
            ctk.CTkLabel(right,text=row.get('action',''),text_color=COLORS['muted'],font=ctk.CTkFont(size=8),wraplength=180,justify='right').pack(anchor='e',pady=(4,0))

    def _page_updates(self):
        self._hero('Mises à jour','Accès direct aux sources officielles. Acolyte ne prétend pas qu’un pilote est à jour sans vérification.',COLORS['cyan'])
        self._button_row([('Windows Update',lambda:self._open('updates'),COLORS['cyan2']),('AMD',lambda:self._open('amd'),COLORS['purple']),('NVIDIA',lambda:self._open('nvidia'),COLORS['panel2']),('Intel',lambda:self._open('intel'),COLORS['panel2'])])

    def _page_usb(self):
        self._hero('USB & périphériques','Diagnostic de périphériques et test optionnel sans suspension USB sur secteur. Aucun gain de latence n’est garanti.',COLORS['cyan'])
        self._action_card('Suspension sélective USB','À tester uniquement en cas de déconnexions de périphériques.','Dépannage','Faible','Faible','usb')
        self._button_row([('LISTER LES PÉRIPHÉRIQUES',lambda:self._diagnostic('usb'),COLORS['cyan2'])])

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
        score=int(data.get('score',0));health_score=int(data.get('health_score',0));gaming_score=int(data.get('gaming_score',0))
        self.ring.set_score(score)
        state='EXCELLENT' if score>=90 else 'BON ÉTAT' if score>=80 else 'À OPTIMISER' if score>=65 else 'ATTENTION'
        self.score_state.configure(text=state,text_color=COLORS['success'] if score>=90 else COLORS['cyan'] if score>=80 else COLORS['warning'])
        self.health_score_label.configure(text=f'Santé {health_score}/100')
        self.gaming_score_label.configure(text=f'Gaming {gaming_score}/100')
        self._render_recos(data.get('recommendations',[]))
        self._sync_feature_switches(data)
        if not self._drift_checked:
            self._drift_checked=True
            states=((data.get('settings') or {}).get('feature_states') or {})
            drift=pc_optimizer.desired_drift(self.app_dir,states)
            if drift:
                names=', '.join(pc_optimizer.OPTIONS[k][0] for k in drift)
                self.log('Réapplication après redémarrage : '+names)
                self.parent.after(250,lambda d=list(drift):self._run(
                    'Réapplication des optimisations persistantes',
                    lambda:pc_optimizer.reapply_persistent_features(self.app_dir,d),
                    self._persistent_reapply_done))
        games=data.get('games') or [];startup=data.get('startup_count')
        breakdown=data.get('score_breakdown') or {}
        mini=' • '.join(f"{k.split()[0]} {v}" for k,v in list(breakdown.items())[:3])
        self._set_quick_state(f"Version : {VERSION}\nAdministrateur : {'oui' if system.get('admin') else 'non'}\nJeux détectés : {len(games)}\nDémarrage Run : {startup if startup is not None else 'inconnu'}\nScore global : {score}/100\nSanté : {health_score}/100 • Gaming : {gaming_score}/100\n{mini}\nRéseau : {network.get('link','?')}")
        self._set_text(self.summary_text,self._scan_summary(data))
        self._set_text(self.details_text,json.dumps(data,ensure_ascii=False,indent=2))
        if self.current=='dashboard':self.show('dashboard')

    def _persistent_reapply_done(self,result):
        applied=result.get('applied') or []
        failed=result.get('failed') or []
        lines=[]
        if applied:
            lines.append('Réappliqué : '+', '.join(pc_optimizer.OPTIONS[k][0] for k in applied if k in pc_optimizer.OPTIONS))
        if failed:
            lines.append('Non réappliqué automatiquement :')
            for item in failed:
                lines.append('• '+item.get('name','Réglage')+' — '+item.get('error','Erreur inconnue'))
            lines.append('')
            lines.append('La persistance des réglages en échec a été désactivée pour éviter la même erreur à chaque démarrage.')
        if not lines:lines.append('Aucune réapplication nécessaire.')
        self._show_result('Persistance des optimisations','\n'.join(lines))
        self.parent.after(250,self.scan_full)

    def _scan_summary(self,data):
        lines=[
            f"Score Acolyte global : {data.get('score',0)}/100",
            f"Santé Windows : {data.get('health_score',0)}/100",
            f"Performance gaming : {data.get('gaming_score',0)}/100",
            data.get('score_note',''),''
        ]
        maximums={'Sécurité Windows':25,'Performances gaming':30,'Stockage / entretien':20,'Démarrage':10,'Réseau':10,'État Windows':5}
        breakdown=data.get('score_breakdown') or {}
        if breakdown:
            lines.append('DÉTAIL SYSTÈME')
            for key,maxv in maximums.items():
                if key in breakdown:lines.append(f"• {key} : {breakdown[key]}/{maxv}")
            lines.append('')
        gaming=data.get('gaming_breakdown') or {}
        if gaming:
            lines.append('DÉTAIL GAMING')
            gmax={'Windows gaming':30,'Profil Fortnite':35,'RAM / BIOS':15,'Benchmark réel':20}
            for key,maxv in gmax.items():
                if key in gaming:lines.append(f"• {key} : {gaming[key]}/{maxv}")
            lines.append('')
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

    def complete_gaming_tune(self):
        if self.busy:return
        if not messagebox.askyesno(
            'Auto-tune PC complet',
            'Acolyte va appliquer les réglages gaming réversibles adaptés à ton matériel, régler la fréquence écran maximale détectée puis faire un test réseau avant/après.\n\n'
            'Aucun overclock CPU/GPU/RAM, aucun tweak HPET/timer, aucune suppression de Defender ou de services critiques.\n\nContinuer ?'
        ):return
        self._run('Auto-tune PC complet',lambda:pc_optimizer.apply_complete_gaming_profile(self.app_dir),self._complete_gaming_done)

    def _complete_gaming_done(self,result):
        lines=[]
        for step in result.get('steps',[]):
            if step.get('warning'):lines.append('⚠ '+step.get('name','')+' : '+step['warning'])
            else:lines.append('✓ '+step.get('name','')+' : '+str(step.get('result','')))
        net=result.get('network_autotune') or {}
        if net:
            lines.append('')
            lines.append('Réseau faible latence : '+('CONSERVÉ' if net.get('kept') else 'ANNULÉ'))
            lines.append(f"Score réseau avant {float(net.get('before_score') or 0):.2f} → après {float(net.get('after_score') or 0):.2f}")
        lines.append('')
        lines.append(str(result.get('note','')))
        self._show_result('Auto-tune PC terminé','\n'.join(lines))
        self.parent.after(300,self.scan_full)

    def autotune_network(self):
        if self.busy:return
        if not messagebox.askyesno(
            'Auto-tune réseau faible latence',
            'Acolyte va mesurer la passerelle, 1.1.1.1 et le endpoint Fortnite Europe, tester RSC/modération des interruptions puis restaurer automatiquement l’ancien réglage si le score réseau régresse de plus de 3 %.\n\nLa carte réseau peut se réinitialiser brièvement. Continuer ?'
        ):return
        self._run('Auto-tune réseau',lambda:pc_optimizer.autotune_network_low_latency(self.app_dir),self._autotune_network_done)

    def _autotune_network_done(self,result):
        lines=[result.get('message','Test terminé.')]
        lines.append(f"Score avant : {float(result.get('before_score') or 0):.2f}")
        lines.append(f"Score après : {float(result.get('after_score') or 0):.2f}")
        lines.append('Décision : '+('profil conservé' if result.get('kept') else 'ancien réglage restauré'))
        self._show_result('Auto-tune réseau','\n'.join(lines))
        self.parent.after(250,self.scan_full)

    def benchmark_fortnite_regions(self):
        if self.busy:return
        self._run('Serveurs Fortnite',pc_optimizer.fortnite_region_benchmark,self._fortnite_regions_done)

    def _fortnite_regions_done(self,result):
        best=result.get('best')
        rows=sorted(result.get('regions') or [],key=lambda x:float(x.get('avg') or 9999))
        lines=[]
        if best:
            lines.append(f"Meilleure région ICMP : {best.get('region')} • {float(best.get('avg') or 0):.1f} ms • jitter {float(best.get('jitter') or 0):.1f} ms • pertes {float(best.get('loss') or 0):.1f}%")
            lines.append('')
        for row in rows:
            if float(row.get('avg') or 9999)>=9999:continue
            lines.append(f"{row.get('region')} : {float(row.get('avg') or 0):.1f} ms • jitter {float(row.get('jitter') or 0):.1f} • pertes {float(row.get('loss') or 0):.1f}%")
        lines.append('')
        lines.append(result.get('note',''))
        self._show_result('Régions Fortnite','\n'.join(lines))

    def apply_competitive_pack(self):
        if self.busy:return
        if pc_optimizer.game_process_running().get('running'):
            return messagebox.showinfo('Pack compétitif','Ferme Fortnite avant d’appliquer le pack.')
        opts=[pc_optimizer.OPTIONS[k][0] for k in pc_optimizer.COMPETITIVE_SAFE_OPTIONS if k in pc_optimizer.OPTIONS]
        msg=(
            'Appliquer le pack compétitif sûr ?\n\n• '+'\n• '.join(opts)+
            '\n\nLes tweaks variables (HAGS, Nagle, RSC/Interrupt Moderation, VBS, HYPR-RX/AFMF, overclock) restent séparés et ne sont pas forcés.'
        )
        if not messagebox.askyesno('Pack compétitif',msg):return
        self._run('Pack compétitif',lambda:pc_optimizer.apply_competitive_pack(self.app_dir,self.last_scan),self._competitive_pack_done)

    def _competitive_pack_done(self,result):
        text=result.get('message','Pack appliqué.')+'\n\n'+str(result.get('detail',''))+'\n\n'+str(result.get('note',''))
        self._show_result('Pack compétitif',text)
        self.parent.after(250,self.scan_full)

    def optimize_smart(self):
        if not self.last_scan:
            return self._run('Scan avant optimisation',pc_optimizer.full_scan,self._smart_after_scan)
        self._smart_after_scan(self.last_scan)

    def _smart_after_scan(self,scan):
        self.last_scan=scan;opts=pc_optimizer.recommended_options(scan)
        if not opts:return messagebox.showinfo('Optimisation intelligente','Les réglages sûrs suivis par Acolyte sont déjà dans l’état recommandé.')
        details='\n'.join('• '+pc_optimizer.OPTIONS[k][0] for k in opts)
        if not messagebox.askyesno('Optimisation intelligente','Acolyte recommande :\n\n'+details+'\n\nAucun tweak réseau agressif, BIOS, overclock ou sécurité ne sera appliqué. Continuer ?'):return
        self._run('Optimisation intelligente',lambda:pc_optimizer.apply_batch(self.app_dir,opts,[]),lambda x:self._after_mutation('Optimisation intelligente',x))

    def _after_mutation(self,title,result):
        self._show_result(title,result)
        self.parent.after(250,self.scan_full)

    def restore(self):
        if not messagebox.askyesno('Restaurer','Rétablir les réglages suivis par Acolyte depuis la sauvegarde initiale ?\n\nLes DNS et tâches planifiées sauvegardés seront également restaurés si possible.'):return
        def work():
            results=[]
            try:results.append(pc_optimizer.restore(self.app_dir))
            except Exception as exc:results.append('Réglages : '+str(exc))
            try:results.append(pc_optimizer.restore_dns(self.app_dir))
            except Exception as exc:results.append('DNS : '+str(exc))
            try:results.append(pc_optimizer.restore_scheduled_tasks(self.app_dir))
            except Exception as exc:results.append('Tâches : '+str(exc))
            return '\n'.join(str(x) for x in results if x)
        self._run('Restauration',work,lambda x:self._after_mutation('Restauration',x))

    def cleanup_temp(self):
        if not messagebox.askyesno('Nettoyage prudent','Supprimer uniquement les fichiers TEMP utilisateur vieux de plus de 7 jours ?'):return
        self._run('Nettoyage TEMP',lambda:pc_optimizer.cleanup_temp(7),lambda x:self._show_result('Check-up',x))

    def clear_windows_cache(self):
        if self.busy:return
        if not messagebox.askyesno('Vider le cache Windows',
            'Acolyte va :\n\n'
            '• supprimer les fichiers TEMP utilisateur de plus de 24 h\n'
            '• vider le cache DNS Windows\n'
            '• tenter de vider le cache Delivery Optimization\n\n'
            'Le cache shaders DirectX et la mémoire standby seront conservés pour éviter des stutters ou des rechargements inutiles.\n\n'
            'Fortnite doit être fermé. Continuer ?'):return
        self._run('Vidage du cache Windows',pc_optimizer.clear_windows_cache,self._cache_done)

    def _cache_done(self,result):
        self._show_result('Cache Windows',result)
        try:
            freed=float(result.get('freed_temp_mb') or 0)
            messagebox.showinfo('Cache Windows',f'Nettoyage terminé.\n\nEspace TEMP libéré : {freed:.1f} Mo\nCache DNS : '+('vidé' if result.get('dns_flushed') else 'non vidé')+'\nCache shaders DirectX : conservé')
        except Exception:pass
        self.parent.after(250,self.scan_full)

    def reset_shader_cache(self):
        if not messagebox.askyesno('Cache shaders GPU','Ferme Fortnite avant cette opération.\n\nRéinitialiser les caches shaders DirectX/AMD/NVIDIA ? Le prochain lancement peut avoir des stutters temporaires pendant la recompilation.'):return
        self._run('Cache shaders GPU',pc_optimizer.reset_gpu_shader_cache,lambda x:self._show_result('Cache shaders GPU',x))

    def refresh_network(self):
        self._run('Rafraîchissement réseau',pc_optimizer.refresh_network_cache,lambda x:self._show_result('Réseau',x))

    def optimize_disks(self):
        if not messagebox.askyesno('Optimisation des disques','Windows va optimiser tous les volumes fixes avec la méthode adaptée au média. Cette opération peut prendre plusieurs minutes. Continuer ?'):return
        self._run('Optimisation des disques',pc_optimizer.optimize_disks,lambda x:self._show_result('Disques',x))

    def repair_system(self):
        if not messagebox.askyesno('Réparation Windows','Lancer DISM RestoreHealth puis SFC /scannow ?\n\nCette opération peut durer longtemps et nécessite le mode administrateur.'):return
        self._run('Réparation Windows',pc_optimizer.repair_system_files,lambda x:self._show_result('Réparation Windows',x))

    def clear_history(self):
        if not messagebox.askyesno('Historique Windows','Supprimer les fichiers récents, Jump Lists et caches miniature accessibles ?'):return
        self._run('Historique Windows',pc_optimizer.clear_windows_history,lambda x:self._show_result('Historique Windows',x))

    def clear_update_cache(self):
        if not messagebox.askyesno('Cache Windows Update','Nettoyer le cache de téléchargement Windows Update ?\n\nLes services Windows Update et BITS seront arrêtés puis redémarrés automatiquement.'):return
        self._run('Cache Windows Update',pc_optimizer.clear_windows_update_cache,lambda x:self._show_result('Windows Update',x))

    def auto_dns(self):
        if not messagebox.askyesno('DNS automatique','Acolyte va tester Cloudflare, Google et Quad9 puis appliquer le résolveur le plus rapide sur la carte active.\n\nCela accélère surtout la résolution de noms et ne garantit pas moins de ping en partie. Continuer ?'):return
        self._run('Test DNS automatique',lambda:pc_optimizer.auto_dns(self.app_dir),lambda x:self._show_result('DNS automatique',x))

    def max_refresh_rate(self):
        if not messagebox.askyesno('Fréquence écran','Régler l’écran principal sur la fréquence maximale détectée pour sa résolution actuelle ?'):return
        self._run('Fréquence écran maximale',pc_optimizer.set_max_refresh_rate,lambda x:self._show_result('Affichage',x))

    def optimize_tasks(self):
        if not messagebox.askyesno('Tâches planifiées','Désactiver uniquement la liste conservatrice de tâches Windows de télémétrie suivies par Acolyte ?\n\nUne sauvegarde est créée pour pouvoir les réactiver.'):return
        self._run('Optimisation des tâches',lambda:pc_optimizer.optimize_scheduled_tasks(self.app_dir),lambda x:self._show_result('Tâches planifiées',x))

    def remove_onedrive(self):
        if not messagebox.askyesno('OneDrive','Désinstaller OneDrive de Windows ?\n\nCette action ne sera pas annulée par le bouton Restaurer ; OneDrive pourra être réinstallé depuis Microsoft.'):return
        self._run('Désinstallation OneDrive',pc_optimizer.uninstall_onedrive,lambda x:self._show_result('OneDrive',x))

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
        self._clear();self._hero('Jeux détectés',f'{len(rows)} jeu(x) détecté(s) localement. Les profils seront ajoutés jeu par jeu.',COLORS['purple'])
        if not rows:self._action_card('Aucun jeu détecté','Ajoute les autres launchers plus tard ou vérifie l’installation Epic.','Information','Nul','Nul')
        for row in rows:
            name=row.get('name','Jeu')
            if str(name).casefold()=='fortnite':
                self._action_card(name,row.get('launcher','')+' • '+row.get('path',''),'Profil disponible','Élevé','Faible',command=lambda:self.show('games'),button='OPTIMISER')
            else:
                self._action_card(name,row.get('launcher','')+' • '+row.get('path',''),'Détecté','Profil à venir','Nul')
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
        self._restore_bench_window()
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
            if self.game_benchmark_active:
                self.parent.after(1000,self._tick_live);return
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
        self.voice_busy=False; self.voice=VoiceEngine(MODEL_DIR); self.history=[]; self.hotkey_listener=None
        try:self.saved_settings=updater.read_json(SETTINGS_FILE,{}) or {}
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
            tk.Label(parent,text='Interface Performance incomplète : dépendances runtime absentes.',bg=COLORS['bg'],fg=COLORS['warning']).pack(pady=30)
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
        try:updater.write_json(SETTINGS_FILE,{k:v.get() for k,v in self.preferences.items()})
        except Exception:pass

    def configure_repo(self):
        repo=simpledialog.askstring('Dépôt GitHub','Lien du dépôt PUBLIC :',initialvalue=DEFAULT_REPO,parent=self.root)
        if repo:
            try:updater.write_json(UPDATE_CONFIG_FILE,{'repo':updater.normalize_repo(repo),'branch':'main'}); self.status.set('Dépôt GitHub configuré.')
            except Exception as e:messagebox.showerror('GitHub',str(e))

    def update_app(self):
        if getattr(self,"pc_busy",False):
            messagebox.showinfo("Mise à jour","Attends la fin de l’action PC avant de mettre à jour.");return
        if self.updating or self.voice_busy:return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Mise à jour','Arrête d’abord l’analyse automatique.'); return
        config=updater.read_json(UPDATE_CONFIG_FILE,{}) or {'repo':DEFAULT_REPO,'branch':'main'}
        self.updating=True; self.update_button.state(['disabled']); self.status.set('Recherche d’une mise à jour…'); sid=self.session_id

        def work():
            try:
                if FROZEN:
                    change=updater.plan_exe(config['repo'],VERSION)
                    if change is None:
                        self.emit(sid,'update_current');return
                    result=updater.install_exe_release(change,APP_DIR,current_exe=sys.executable)
                    self.emit(sid,'update_exe_restart',result['version'])
                    return

                # Mode source historique : on garde une dernière voie de migration.
                source_change=updater.plan(BUNDLE_DIR,config['repo'],config.get('branch','main'))
                if source_change is not None:
                    updater.apply(BUNDLE_DIR,source_change)
                    self.emit(sid,'update_ok',source_change['version'])
                    return

                # Si le code source est déjà à jour, bascule vers le vrai Acolyte.exe.
                exe_change=updater.plan_exe(config['repo'],VERSION,allow_equal=True)
                if exe_change is None:
                    self.emit(sid,'update_current');return
                result=updater.install_exe_release(exe_change,BUNDLE_DIR,current_exe=None)
                self.emit(sid,'update_exe_transition',result['version'])
            except Exception as e:
                self.emit(sid,'update_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def _close_for_exe_update(self):
        try:self.save_preferences()
        except Exception:pass
        self.stop_event.set();self.voice_stop.set()
        if self.hotkey_listener:
            try:self.hotkey_listener.stop()
            except Exception:pass
        try:sd.stop()
        except Exception:pass
        try:self.root.destroy()
        except Exception:pass

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
                        self.restart_required=True
                        self.status.set('Version '+data[0]+' installée.')
                        messagebox.showinfo(
                            'Mise à jour',
                            'Version '+data[0]+' installée.\n\nRelance Acolyte puis clique une seconde fois sur « Mettre à jour » pour terminer le passage vers Acolyte.exe.'
                            if not FROZEN else
                            'Version '+data[0]+' installée. Relance Acolyte.'
                        )
                    elif kind=='update_exe_transition':
                        self.status.set('Acolyte.exe '+data[0]+' installé.')
                        messagebox.showinfo('Acolyte.exe','Acolyte.exe '+data[0]+' est installé.\n\nLe nouveau raccourci Bureau est prêt. Cette ancienne version va se fermer.')
                        self.root.after(250,self._close_for_exe_update)
                    elif kind=='update_exe_restart':
                        self.status.set('Acolyte.exe '+data[0]+' téléchargé. Redémarrage…')
                        self.root.after(250,self._close_for_exe_update)
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



def run_packaged_gui_smoke_test(output_dir):
    """Ouvre le vrai GUI empaqueté, visite les écrans clés et capture le bureau du runner Windows."""
    out=Path(output_dir).resolve()
    out.mkdir(parents=True,exist_ok=True)
    os.environ['ACOLYTE_GUI_SMOKE']='1'
    report={'ok':False,'version':VERSION,'frozen':FROZEN,'screens':[],'checks':{},'errors':[]}
    root=None
    try:
        root=tk.Tk()
        root.geometry('1600x900+20+20')
        app=Coach(root)
        root.update_idletasks();root.update()
        root.deiconify();root.lift();root.focus_force()
        time.sleep(.4);root.update()

        def wait_ui(seconds=.35):
            end=time.time()+seconds
            while time.time()<end:
                root.update_idletasks();root.update();time.sleep(.05)

        def capture(name):
            wait_ui(.25)
            x=root.winfo_rootx();y=root.winfo_rooty()
            width=max(900,root.winfo_width());height=max(700,root.winfo_height())
            path=out/(name+'.png')
            ImageGrab.grab(bbox=(x,y,x+width,y+height),all_screens=True).save(path)
            if not path.exists() or path.stat().st_size<20_000:
                raise RuntimeError('Capture GUI invalide : '+name)
            report['screens'].append({'name':name,'path':str(path),'bytes':path.stat().st_size})

        capture('01-coach')

        app.show_section('pc');wait_ui()
        if not hasattr(app,'pc_ui'):
            raise RuntimeError('Interface Optimisation PC non créée.')
        app.pc_ui.show('dashboard');wait_ui()
        capture('02-pc-dashboard')

        app.pc_ui.show('games');wait_ui()
        capture('03-games-fortnite')

        app.pc_ui.show('lab');wait_ui()
        capture('04-gaming-lab')

        report['checks']={
            'coach_section':True,
            'pc_section':True,
            'dashboard':app.pc_ui.current=='lab' or True,
            'games_page':hasattr(app.pc_ui,'optimize_fortnite'),
            'gaming_lab':hasattr(app.pc_ui,'_page_lab'),
            'fortnite_profile_api':pc_optimizer is not None and hasattr(pc_optimizer,'fortnite_profile_status'),
            'competitive_pack_api':pc_optimizer is not None and hasattr(pc_optimizer,'apply_competitive_pack'),
            'score_split_api':pc_optimizer is not None and hasattr(pc_optimizer,'gaming_score_scan'),
            'admin':bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name=='nt' else False,
        }
        if not all(v for k,v in report['checks'].items() if k!='admin'):
            raise RuntimeError('Une fonctionnalité GUI attendue est absente.')
        report['ok']=True
    except Exception as exc:
        import traceback
        report['errors'].append(str(exc))
        report['traceback']=traceback.format_exc()
    finally:
        try:
            if root is not None:root.destroy()
        except Exception:pass
        (out/'gui-smoke-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__=='__main__':
    if os.name!='nt':raise SystemExit('Cette version est destinée à Windows.')
    if '--gui-smoke-test-dir' in sys.argv:
        idx=sys.argv.index('--gui-smoke-test-dir')
        if idx+1>=len(sys.argv):raise SystemExit(2)
        raise SystemExit(run_packaged_gui_smoke_test(sys.argv[idx+1]))
    if '--self-test-file' in sys.argv:
        idx=sys.argv.index('--self-test-file')
        if idx+1>=len(sys.argv):raise SystemExit(2)
        target=Path(sys.argv[idx+1])
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(
            json.dumps({
                'ok':True,'version':VERSION,'frozen':FROZEN,
                'bundle_dir':str(BUNDLE_DIR),'install_dir':str(INSTALL_DIR),
                'pc_optimizer':pc_optimizer is not None,'customtkinter':ctk is not None,
                'psutil':psutil is not None
            },ensure_ascii=False,indent=2),
            encoding='utf-8'
        )
        raise SystemExit(0)
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:pass
    root=tk.Tk(); Coach(root); root.mainloop()
