import base64, ctypes, io, os, queue, subprocess, sys, tempfile, threading, time, wave
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
import updater

APP_DIR=Path(__file__).resolve().parent
DEFAULT_REPO='Clemen5t/Coach_Fortnite_Ia'
VERSION=(APP_DIR/'VERSION').read_text().strip()

import mss
import numpy as np
import sounddevice as sd
from PIL import Image, ImageTk
from local_ai import LocalAI, usable, AdviceGate

VOICE_NAME='fr_FR-siwis-medium'
WHISPER_MODEL='base'

# Logo dessiné en vectoriel dans Tkinter pour rester compatible avec l'updater existant.

COLORS={
    'bg':'#070b18',
    'panel':'#10182b',
    'panel2':'#141f38',
    'panel3':'#0c1325',
    'border':'#253455',
    'cyan':'#22d3ee',
    'cyan2':'#0ea5e9',
    'purple':'#7c3aed',
    'purple2':'#a855f7',
    'gold':'#facc15',
    'text':'#f8fafc',
    'muted':'#94a3b8',
    'success':'#22c55e',
    'danger':'#ef4444',
    'warning':'#f59e0b',
    'log':'#08101f',
}

def active_game():
    buf=ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(ctypes.windll.user32.GetForegroundWindow(),buf,512)
    return 'fortnite' in buf.value.lower()

class VoiceEngine:
    def __init__(self,app_dir):
        self.root=Path(app_dir)
        self.data=self.root/'.voice'; self.data.mkdir(exist_ok=True)
        self.whisper_dir=self.root/'.whisper'; self.whisper_dir.mkdir(exist_ok=True)
        self.whisper=None; self.piper=None; self.lock=threading.RLock()

    @property
    def voice_model(self):
        return self.data/(VOICE_NAME+'.onnx')

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
            status('Chargement de Whisper local sur CPU…')
            from faster_whisper import WhisperModel
            self.whisper=WhisperModel(WHISPER_MODEL,device='cpu',compute_type='int8',download_root=str(self.whisper_dir))

    def prepare(self,status=lambda x:None):
        with self.lock:
            self.ensure_tts(status); self.ensure_whisper(status)

    def record_question(self,status=lambda x:None,level=lambda rms,peak,bands:None,seconds=4.5,device=None):
        with self.lock:
            self.ensure_whisper(status)
            status('🎙️ Parle maintenant…')
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
                else:
                    bands=[0.0]*7
                level(rms,peak,bands)
            try:
                with sd.InputStream(device=device,samplerate=rate,channels=1,dtype='float32',blocksize=block,callback=callback):
                    end=time.monotonic()+seconds
                    while time.monotonic()<end: time.sleep(.03)
            except Exception as e:
                raise RuntimeError('Impossible d’ouvrir le microphone sélectionné : '+str(e)) from e
            level(0.0,0.0,[0.0]*7)
            samples=np.concatenate(chunks) if chunks else np.zeros(0,dtype=np.float32)
            if samples.size==0:
                raise RuntimeError('Aucun échantillon reçu du microphone sélectionné.')
            status('Transcription locale…')
            segments,_=self.whisper.transcribe(samples,language='fr',beam_size=1,best_of=1,vad_filter=True,condition_on_previous_text=False,temperature=0)
            return ' '.join(s.text.strip() for s in segments if s.text.strip()).strip()

    def speak(self,text,status=lambda x:None):
        if not text: return
        with self.lock:
            self.ensure_tts(status); status('🔊 Acolyte répond…')
            fd,path=tempfile.mkstemp(suffix='.wav'); os.close(fd)
            try:
                with wave.open(path,'wb') as wav_file:
                    self.piper.synthesize_wav(text,wav_file)
                with wave.open(path,'rb') as wav_file:
                    rate=wav_file.getframerate(); channels=wav_file.getnchannels(); width=wav_file.getsampwidth(); raw=wav_file.readframes(wav_file.getnframes())
                if width==2: data=np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768.0
                elif width==4: data=np.frombuffer(raw,dtype=np.int32).astype(np.float32)/2147483648.0
                else: raise RuntimeError('Format audio Piper non pris en charge.')
                if channels>1: data=data.reshape(-1,channels)
                sd.play(data,rate); sd.wait()
            finally:
                try: os.unlink(path)
                except OSError: pass

class Coach:
    def __init__(self,root):
        self.root=root; self.events=queue.Queue(); self.stop_event=threading.Event(); self.voice_stop=threading.Event()
        self.worker=None; self.client=None; self.session_id=0; self.updating=False; self.restart_required=False
        self.voice_busy=False; self.voice=VoiceEngine(APP_DIR); self.history=[]; self.hotkey_listener=None
        try: self.saved_settings=updater.read_json(APP_DIR/'settings.json',{}) or {}
        except Exception: self.saved_settings={}

        self.build_theme()
        root.title('Acolyte Fortnite — créé par Clemen4t — '+VERSION)
        root.geometry('1180x860'); root.minsize(1050,760); root.configure(bg=COLORS['bg'])

        shell=tk.Frame(root,bg=COLORS['bg']); shell.pack(fill='both',expand=True)
        self.build_header(shell)

        content=tk.Frame(shell,bg=COLORS['bg']); content.pack(fill='both',expand=True,padx=22,pady=(0,14))
        content.grid_columnconfigure(0,weight=3,uniform='main'); content.grid_columnconfigure(1,weight=2,uniform='main')
        content.grid_rowconfigure(1,weight=1)

        left=tk.Frame(content,bg=COLORS['bg']); left.grid(row=0,column=0,rowspan=2,sticky='nsew',padx=(0,8))
        right=tk.Frame(content,bg=COLORS['bg']); right.grid(row=0,column=1,rowspan=2,sticky='nsew',padx=(8,0))

        self.build_voice_card(left)
        self.build_response_card(left)
        self.build_log_card(left)
        self.build_auto_card(right)
        self.build_system_card(right)
        self.build_footer(shell)

        self.refresh_microphones(initial=True)
        self.install_hotkey(); root.protocol('WM_DELETE_WINDOW',self.close); root.after(50,self.poll)

    def build_theme(self):
        style=ttk.Style()
        try: style.theme_use('clam')
        except tk.TclError: pass
        style.configure('Dark.TCombobox',fieldbackground=COLORS['panel3'],background=COLORS['panel3'],foreground=COLORS['text'],arrowcolor=COLORS['cyan'],bordercolor=COLORS['border'],lightcolor=COLORS['border'],darkcolor=COLORS['border'],padding=6)
        style.map('Dark.TCombobox',fieldbackground=[('readonly',COLORS['panel3'])],foreground=[('readonly',COLORS['text'])],selectbackground=[('readonly',COLORS['panel3'])],selectforeground=[('readonly',COLORS['text'])])
        style.configure('Dark.TEntry',fieldbackground=COLORS['panel3'],foreground=COLORS['text'],insertcolor=COLORS['text'],bordercolor=COLORS['border'],padding=6)
        style.configure('Dark.TCheckbutton',background=COLORS['panel'],foreground=COLORS['text'],focuscolor=COLORS['panel'])
        style.map('Dark.TCheckbutton',background=[('active',COLORS['panel'])],foreground=[('active',COLORS['cyan'])])
        for name,bg,fg in (
            ('Primary.TButton',COLORS['purple'],COLORS['text']),
            ('Cyan.TButton',COLORS['cyan2'],COLORS['text']),
            ('Gold.TButton',COLORS['gold'],'#111827'),
            ('Danger.TButton',COLORS['danger'],COLORS['text']),
            ('Ghost.TButton',COLORS['panel2'],COLORS['text']),
        ):
            style.configure(name,background=bg,foreground=fg,borderwidth=0,padding=(13,9),font=('Segoe UI',10,'bold'))
            style.map(name,background=[('active',self.mix(bg,'#ffffff',0.12)),('disabled',COLORS['border'])],foreground=[('disabled',COLORS['muted'])])

    @staticmethod
    def mix(a,b,t):
        try:
            aa=[int(a[i:i+2],16) for i in (1,3,5)]; bb=[int(b[i:i+2],16) for i in (1,3,5)]
            return '#'+''.join(f'{round(x+(y-x)*t):02x}' for x,y in zip(aa,bb))
        except Exception:
            return a

    def card(self,parent,title,subtitle=None,accent=None,pady=(0,14)):
        accent=accent or COLORS['cyan']
        outer=tk.Frame(parent,bg=COLORS['border']); outer.pack(fill='x',pady=pady)
        inner=tk.Frame(outer,bg=COLORS['panel']); inner.pack(fill='both',expand=True,padx=1,pady=1)
        top=tk.Frame(inner,bg=COLORS['panel']); top.pack(fill='x',padx=16,pady=(14,8))
        tk.Frame(top,bg=accent,width=5,height=34).pack(side='left',padx=(0,10));
        heading=tk.Frame(top,bg=COLORS['panel']); heading.pack(side='left',fill='x',expand=True)
        tk.Label(heading,text=title,bg=COLORS['panel'],fg=COLORS['text'],font=('Segoe UI',13,'bold')).pack(anchor='w')
        if subtitle:
            tk.Label(heading,text=subtitle,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),wraplength=650,justify='left').pack(anchor='w',pady=(2,0))
        body=tk.Frame(inner,bg=COLORS['panel']); body.pack(fill='both',expand=True,padx=16,pady=(0,14))
        return body

    def build_header(self,parent):
        header=tk.Frame(parent,bg=COLORS['bg']); header.pack(fill='x',padx=22,pady=(18,14))
        logo_box=tk.Canvas(header,bg=COLORS['panel3'],highlightbackground=COLORS['cyan'],highlightthickness=1,width=148,height=94,bd=0)
        logo_box.pack(side='left')
        logo_box.create_polygon(74,7,132,34,119,82,74,91,29,82,16,34,fill='#101a38',outline=COLORS['purple2'],width=3)
        logo_box.create_polygon(74,16,119,38,108,72,74,82,40,72,29,38,fill='#0a1228',outline=COLORS['cyan'],width=2)
        logo_box.create_polygon(48,48,74,27,100,48,88,58,74,46,60,58,fill=COLORS['cyan'],outline='')
        logo_box.create_text(74,69,text='AI',fill=COLORS['text'],font=('Segoe UI Black',18,'bold'))
        logo_box.create_text(74,88,text='ACOLYTE',fill=COLORS['gold'],font=('Segoe UI',7,'bold'))

        titlebox=tk.Frame(header,bg=COLORS['bg']); titlebox.pack(side='left',fill='x',expand=True,padx=18)
        tk.Label(titlebox,text='ACOLYTE FORTNITE',bg=COLORS['bg'],fg=COLORS['text'],font=('Segoe UI Black',27,'bold')).pack(anchor='w')
        tk.Label(titlebox,text='Ton coéquipier IA local • créé par Clemen4t',bg=COLORS['bg'],fg=COLORS['cyan'],font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(2,4))
        tk.Label(titlebox,text='Whisper + Gemma 3 Vision + Piper • aucune clé API • analyse à la demande',bg=COLORS['bg'],fg=COLORS['muted'],font=('Segoe UI',9)).pack(anchor='w')

        badges=tk.Frame(header,bg=COLORS['bg']); badges.pack(side='right',anchor='ne')
        tk.Label(badges,text=' v'+VERSION+' ',bg=COLORS['purple'],fg=COLORS['text'],font=('Segoe UI',9,'bold'),padx=8,pady=5).pack(side='right',padx=(6,0))
        tk.Label(badges,text=' 100% LOCAL ',bg=COLORS['gold'],fg='#111827',font=('Segoe UI',9,'bold'),padx=8,pady=5).pack(side='right')

        line=tk.Frame(parent,bg=COLORS['cyan'],height=2); line.pack(fill='x',padx=22,pady=(0,16))

    def build_voice_card(self,parent):
        body=self.card(parent,'ACOLYTE VOCAL','Appuie sur F8, pose ta question : Acolyte écoute, regarde ton écran et te répond.','%s'%COLORS['purple'])
        self.mic_choice=tk.StringVar(value=str(self.saved_settings.get('microphone','')))
        self.mic_devices={}; self.mic_label=tk.StringVar(value='Micro : détection…')

        tk.Label(body,text='MICROPHONE',bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).pack(anchor='w')
        microw=tk.Frame(body,bg=COLORS['panel']); microw.pack(fill='x',pady=(5,10))
        self.mic_combo=ttk.Combobox(microw,textvariable=self.mic_choice,state='readonly',style='Dark.TCombobox')
        self.mic_combo.pack(side='left',fill='x',expand=True)
        ttk.Button(microw,text='↻ Actualiser',command=self.refresh_microphones,style='Ghost.TButton').pack(side='left',padx=(8,0))
        self.mic_combo.bind('<<ComboboxSelected>>',lambda e:self.on_microphone_changed())

        actions=tk.Frame(body,bg=COLORS['panel']); actions.pack(fill='x')
        self.talk_button=ttk.Button(actions,text='🎙  PARLER AU COACH  •  F8',command=self.ask_voice,style='Primary.TButton'); self.talk_button.pack(side='left')
        ttk.Button(actions,text='Préparer la voix IA',command=self.prepare_voice,style='Cyan.TButton').pack(side='left',padx=8)
        self.voice_state=tk.StringVar(value='Prêt. F8 = parler au coach.')
        tk.Label(body,textvariable=self.voice_state,bg=COLORS['panel'],fg=COLORS['cyan'],font=('Segoe UI',10,'bold'),wraplength=650,justify='left').pack(anchor='w',pady=(10,6))

        meterrow=tk.Frame(body,bg=COLORS['panel']); meterrow.pack(fill='x')
        tk.Label(meterrow,textvariable=self.mic_label,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),width=28,anchor='w').pack(side='left')
        self.meter=tk.Canvas(meterrow,height=38,highlightthickness=1,highlightbackground=COLORS['cyan2'],bg=COLORS['panel3'],bd=0)
        self.meter.pack(side='left',fill='x',expand=True,padx=(8,0))
        self.audio_bars=[]
        for i in range(20):
            x1=6+i*23; x2=x1+15
            self.audio_bars.append(self.meter.create_rectangle(x1,30,x2,32,fill='#26334f',outline=''))

    def build_response_card(self,parent):
        body=self.card(parent,'RÉPONSE DU COACH','Le conseil le plus récent reste visible ici.',COLORS['cyan'])
        self.status=tk.StringVar(value='Mode vocal prêt.')
        self.metric=tk.StringVar(value='Aucune analyse en cours.')
        self.advice=tk.StringVar(value='Appuie sur F8 puis parle.')

        info=tk.Frame(body,bg=COLORS['panel']); info.pack(fill='x',pady=(0,8))
        tk.Label(info,textvariable=self.status,bg=COLORS['panel'],fg=COLORS['success'],font=('Segoe UI',9,'bold')).pack(side='left')
        tk.Label(info,textvariable=self.metric,bg=COLORS['panel'],fg=COLORS['muted'],font=('Consolas',9)).pack(side='right')
        answer=tk.Frame(body,bg=COLORS['panel3'],highlightbackground=COLORS['purple'],highlightthickness=1)
        answer.pack(fill='x')
        tk.Label(answer,text='ACOLYTE',bg=COLORS['panel3'],fg=COLORS['purple2'],font=('Segoe UI',8,'bold')).pack(anchor='w',padx=14,pady=(12,2))
        tk.Label(answer,textvariable=self.advice,bg=COLORS['panel3'],fg=COLORS['text'],font=('Segoe UI',15,'bold'),wraplength=665,justify='left').pack(anchor='w',fill='x',padx=14,pady=(0,14))

    def build_log_card(self,parent):
        body=self.card(parent,'JOURNAL DE PARTIE','Tes questions et les réponses d’Acolyte.',COLORS['gold'],pady=(0,0))
        self.log=tk.Text(body,height=9,wrap='word',bg=COLORS['log'],fg='#dbeafe',insertbackground=COLORS['text'],relief='flat',bd=0,font=('Consolas',9),padx=12,pady=10,selectbackground=COLORS['purple'])
        self.log.pack(fill='both',expand=True)
        self.log.insert('end','ACOLYTE prêt • F8 pour parler au coach\n')

    def build_auto_card(self,parent):
        body=self.card(parent,'ANALYSE AUTOMATIQUE','Optionnelle. Garde un intervalle élevé pour protéger tes FPS.',COLORS['gold'])
        self.monitor=tk.IntVar(value=1); self.interval=tk.DoubleVar(value=4.0); self.expiry=tk.DoubleVar(value=2.5); self.minutes=tk.IntVar(value=5)
        self.onlygame=tk.BooleanVar(value=True); self.speak=tk.BooleanVar(value=True)
        self.preferences={'monitor':self.monitor,'interval':self.interval,'expiry':self.expiry,'minutes':self.minutes,'onlygame':self.onlygame,'speak':self.speak,'microphone':self.mic_choice}
        for name,var in self.preferences.items():
            if name in self.saved_settings and name!='microphone':
                try: var.set(self.saved_settings[name])
                except Exception: pass

        grid=tk.Frame(body,bg=COLORS['panel']); grid.pack(fill='x')
        for r,(label,var) in enumerate([('Écran',self.monitor),('Intervalle',self.interval),('Rejet après',self.expiry),('Durée',self.minutes)]):
            suffix={'Écran':'','Intervalle':' s','Rejet après':' s','Durée':' min'}[label]
            tk.Label(grid,text=label.upper(),bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).grid(row=r,column=0,sticky='w',pady=5)
            entry=ttk.Entry(grid,textvariable=var,width=12,style='Dark.TEntry'); entry.grid(row=r,column=1,sticky='e',padx=(18,4),pady=5)
            tk.Label(grid,text=suffix,bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',8)).grid(row=r,column=2,sticky='w')
        grid.columnconfigure(0,weight=1)
        ttk.Checkbutton(body,text='Seulement quand Fortnite est au premier plan',variable=self.onlygame,style='Dark.TCheckbutton').pack(anchor='w',pady=(8,2))
        ttk.Checkbutton(body,text='Lire aussi les conseils auto avec Piper',variable=self.speak,style='Dark.TCheckbutton').pack(anchor='w')
        row=tk.Frame(body,bg=COLORS['panel']); row.pack(fill='x',pady=(12,0))
        self.start_button=ttk.Button(row,text='▶ Démarrer auto',command=self.start,style='Cyan.TButton'); self.start_button.pack(side='left')
        ttk.Button(row,text='■ Arrêter',command=self.stop,style='Danger.TButton').pack(side='left',padx=8)
        ttk.Button(row,text='Aperçu écran',command=self.preview,style='Ghost.TButton').pack(side='left')

    def build_system_card(self,parent):
        body=self.card(parent,'SYSTÈME','Mises à jour et connexion au dépôt.',COLORS['purple'])
        tk.Label(body,text='Les modèles restent sur ton PC. Aucun abonnement ni clé API.',bg=COLORS['panel'],fg=COLORS['muted'],font=('Segoe UI',9),wraplength=360,justify='left').pack(anchor='w',pady=(0,10))
        row=tk.Frame(body,bg=COLORS['panel']); row.pack(fill='x')
        self.update_button=ttk.Button(row,text='⬇ Mettre à jour',command=self.update_app,style='Gold.TButton'); self.update_button.pack(side='left')
        ttk.Button(row,text='GitHub',command=self.configure_repo,style='Ghost.TButton').pack(side='left',padx=8)

        statusbox=tk.Frame(body,bg=COLORS['panel3'],highlightbackground=COLORS['border'],highlightthickness=1); statusbox.pack(fill='x',pady=(12,0))
        tk.Label(statusbox,text='BUILD',bg=COLORS['panel3'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).pack(anchor='w',padx=12,pady=(10,0))
        tk.Label(statusbox,text='Acolyte '+VERSION,bg=COLORS['panel3'],fg=COLORS['cyan'],font=('Segoe UI',11,'bold')).pack(anchor='w',padx=12,pady=(2,2))
        tk.Label(statusbox,text='créé par Clemen4t',bg=COLORS['panel3'],fg=COLORS['gold'],font=('Segoe UI',9,'bold')).pack(anchor='w',padx=12,pady=(0,10))

    def build_footer(self,parent):
        footer=tk.Frame(parent,bg=COLORS['bg']); footer.pack(fill='x',padx=22,pady=(0,12))
        tk.Label(footer,text='ACOLYTE FORTNITE',bg=COLORS['bg'],fg=COLORS['muted'],font=('Segoe UI',8,'bold')).pack(side='left')
        tk.Label(footer,text='créé par Clemen4t',bg=COLORS['bg'],fg=COLORS['cyan'],font=('Segoe UI',8,'bold')).pack(side='right')

    def refresh_microphones(self,initial=False):
        previous=self.mic_choice.get().strip()
        try:
            devices=sd.query_devices(); values=[]; mapping={}
            try: default_input=int(sd.default.device[0])
            except Exception: default_input=None
            for index,dev in enumerate(devices):
                if int(dev.get('max_input_channels',0) or 0)<=0: continue
                name=str(dev.get('name','Microphone')).strip()
                try: host=str(sd.query_hostapis(int(dev.get('hostapi',0))).get('name','')).strip()
                except Exception: host=''
                label=f'{index} — {name}' + (f' [{host}]' if host else '')
                values.append(label); mapping[label]=index
            self.mic_devices=mapping; self.mic_combo['values']=values
            selected=previous if previous in mapping else ''
            if not selected and previous:
                old=previous.split(' — ',1)[-1].split(' [',1)[0].strip().lower()
                selected=next((x for x in values if x.split(' — ',1)[-1].split(' [',1)[0].strip().lower()==old),'')
            if not selected and default_input is not None:
                selected=next((label for label,idx in mapping.items() if idx==default_input),'')
            if not selected and values: selected=values[0]
            self.mic_choice.set(selected)
            self.mic_label.set('Micro sélectionné : '+selected.split(' — ',1)[-1][:39] if selected else 'Micro : aucun périphérique')
            if not initial: self.save_preferences()
        except Exception as e:
            self.mic_devices={}; self.mic_combo['values']=(); self.mic_choice.set(''); self.mic_label.set('Micro : erreur de détection')
            if not initial: messagebox.showerror('Microphone',str(e))

    def selected_microphone(self):
        label=self.mic_choice.get().strip()
        if label not in self.mic_devices:
            self.refresh_microphones(initial=True); label=self.mic_choice.get().strip()
        if label not in self.mic_devices: raise RuntimeError('Aucun microphone valide sélectionné.')
        return self.mic_devices[label]

    def on_microphone_changed(self):
        label=self.mic_choice.get().strip(); self.save_preferences()
        if label: self.mic_label.set('Micro sélectionné : '+label.split(' — ',1)[-1][:39])

    def install_hotkey(self):
        try:
            from pynput import keyboard
            def on_press(key):
                if key==keyboard.Key.f8: self.root.after(0,self.ask_voice)
            self.hotkey_listener=keyboard.Listener(on_press=on_press); self.hotkey_listener.daemon=True; self.hotkey_listener.start()
        except Exception as e:
            self.voice_state.set('F8 indisponible : utilise le bouton. '+str(e))

    def emit(self,sid,kind,*data): self.events.put((sid,kind,data))

    def capture_for_ai(self):
        with mss.mss() as cap:
            monitor=self.monitor.get()
            if monitor<1 or monitor>=len(cap.monitors): raise ValueError('Numéro d’écran inexistant.')
            shot=cap.grab(cap.monitors[monitor]); im=Image.frombytes('RGB',shot.size,shot.rgb); im.thumbnail((768,432))
            data=io.BytesIO(); im.save(data,format='JPEG',quality=68)
            return base64.b64encode(data.getvalue()).decode()

    def prepare_voice(self):
        if self.voice_busy: return
        self.voice_busy=True; self.talk_button.state(['disabled']); sid=self.session_id
        def work():
            try: self.voice.prepare(lambda s:self.emit(sid,'voice_status',s)); self.emit(sid,'voice_ready')
            except Exception as e: self.emit(sid,'voice_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def ask_voice(self):
        if self.voice_busy or self.updating: return
        try: mic=self.selected_microphone()
        except Exception as e: messagebox.showerror('Microphone',str(e)); return
        self.voice_busy=True; self.talk_button.state(['disabled']); sid=self.session_id
        def work():
            try:
                q=self.voice.record_question(lambda s:self.emit(sid,'voice_status',s),lambda rms,peak,bands:self.emit(sid,'audio_level',rms,peak,bands),device=mic)
                if not q: self.emit(sid,'voice_error','Aucune phrase détectée. Vérifie le spectre et le micro choisi.'); return
                self.emit(sid,'question',q); self.emit(sid,'voice_status','📸 Analyse de l’écran…')
                ai=LocalAI(self.voice_stop); ai.verify(); image=self.capture_for_ai()
                hist='\n'.join(f'Joueur: {x}\nAcolyte: {y}' for x,y in self.history[-4:])
                started=time.monotonic(); a=ai.ask(image,q,hist,timeout=40); elapsed=time.monotonic()-started
                self.history.append((q,a)); self.history=self.history[-6:]
                self.emit(sid,'answer',q,a,elapsed); self.voice.speak(a,lambda s:self.emit(sid,'voice_status',s)); self.emit(sid,'voice_ready')
            except Exception as e: self.emit(sid,'voice_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def preview(self):
        try:
            with mss.mss() as cap:
                shot=cap.grab(cap.monitors[self.monitor.get()]); im=Image.frombytes('RGB',shot.size,shot.rgb)
            im.thumbnail((960,540)); w=tk.Toplevel(self.root); w.title('Aperçu local'); w.configure(bg=COLORS['bg'])
            photo=ImageTk.PhotoImage(im); label=tk.Label(w,image=photo,bg=COLORS['bg']); label.image=photo; label.pack(padx=10,pady=10)
        except Exception as e: messagebox.showerror('Capture',str(e))

    def start(self):
        if self.worker and self.worker.is_alive(): return
        cfg={'monitor':self.monitor.get(),'interval':max(2.0,self.interval.get()),'expiry':self.expiry.get(),'minutes':self.minutes.get(),'onlygame':self.onlygame.get()}
        self.save_preferences(); self.session_id+=1; self.stop_event.clear(); self.start_button.state(['disabled'])
        self.worker=threading.Thread(target=self.run_auto,args=(cfg,self.session_id),daemon=True); self.worker.start()

    def run_auto(self,cfg,sid):
        client=LocalAI(self.stop_event); self.client=client
        try:
            client.verify(); deadline=time.monotonic()+cfg['minutes']*60; previous=''; count=advice_count=silent_count=0; gate=AdviceGate()
            self.emit(sid,'status','Analyse automatique active.')
            while not self.stop_event.is_set() and time.monotonic()<deadline:
                if cfg['onlygame'] and not active_game(): self.stop_event.wait(.5); continue
                started=time.monotonic(); text=client.generate(self.capture_for_ai(),previous,timeout=30); age=time.monotonic()-started; count+=1
                valid=usable(text,age,cfg['expiry'],previous) and gate.allow(client.last_category,time.monotonic())
                if valid: advice_count+=1; previous=text
                else: silent_count+=1
                self.emit(sid,'result',age,count,text,valid,client.last_category,advice_count,silent_count)
                self.stop_event.wait(max(0,cfg['interval']-(time.monotonic()-started)))
        except Exception as e:
            if not self.stop_event.is_set(): self.emit(sid,'status','Arrêt : '+str(e))
        finally:
            self.client=None; self.emit(sid,'finished')

    def save_preferences(self):
        try: updater.write_json(APP_DIR/'settings.json',{k:v.get() for k,v in self.preferences.items()})
        except Exception: pass

    def configure_repo(self):
        repo=simpledialog.askstring('Dépôt GitHub','Lien du dépôt PUBLIC :',initialvalue=DEFAULT_REPO,parent=self.root)
        if repo:
            try: updater.write_json(APP_DIR/'update-config.json',{'repo':updater.normalize_repo(repo),'branch':'main'}); self.status.set('Dépôt GitHub configuré.')
            except Exception as e: messagebox.showerror('GitHub',str(e))

    def update_app(self):
        if self.updating or self.voice_busy: return
        if self.worker and self.worker.is_alive(): messagebox.showinfo('Mise à jour','Arrête d’abord l’analyse automatique.'); return
        config=updater.read_json(APP_DIR/'update-config.json',{}) or {'repo':DEFAULT_REPO,'branch':'main'}
        self.updating=True; self.update_button.state(['disabled']); self.status.set('Recherche d’une mise à jour…'); sid=self.session_id
        def work():
            try:
                change=updater.plan(APP_DIR,config['repo'],config.get('branch','main'))
                if change is None: self.emit(sid,'update_current'); return
                updater.apply(APP_DIR,change); self.emit(sid,'update_ok',change['version'])
            except Exception as e: self.emit(sid,'update_error',str(e))
        threading.Thread(target=work,daemon=True).start()

    def draw_audio(self,rms,peak,bands):
        level=min(1.0,max(0.0,rms*12.0)); active=int(round(level*20))
        for i,item in enumerate(self.audio_bars):
            height=3; fill='#26334f'
            if i<active:
                band=bands[min(len(bands)-1,int(i*len(bands)/20))] if bands else 0.0
                height=6+int(24*max(level,band*.75))
                if peak>=.98: fill=COLORS['danger']
                elif peak>=.85: fill=COLORS['warning']
                elif i>14: fill=COLORS['purple2']
                else: fill=COLORS['cyan']
            x1=6+i*23; x2=x1+15; self.meter.coords(item,x1,34-height,x2,32); self.meter.itemconfigure(item,fill=fill)
        if peak>=.98: self.mic_label.set('Micro : saturation')
        elif rms>.025: self.mic_label.set('Micro : voix détectée')
        elif rms>.004: self.mic_label.set('Micro : signal faible')
        else: self.mic_label.set('Micro : silence')

    def poll(self):
        try:
            while True:
                sid,kind,data=self.events.get_nowait()
                if sid!=self.session_id and not kind.startswith('update_'): continue
                if kind=='audio_level': self.draw_audio(*data)
                elif kind=='voice_status': self.voice_state.set(data[0])
                elif kind=='voice_ready':
                    self.voice_busy=False; self.talk_button.state(['!disabled']); self.voice_state.set('Prêt. F8 = parler au coach.'); self.status.set('Acolyte prêt.')
                elif kind=='voice_error':
                    self.voice_busy=False; self.talk_button.state(['!disabled']); self.voice_state.set('Erreur : '+data[0]); self.status.set('Erreur du mode vocal.')
                elif kind=='question':
                    self.log.insert('end','\n🎙 TOI > '+data[0]+'\n'); self.log.see('end')
                elif kind=='answer':
                    q,a,elapsed=data; self.advice.set(a); self.metric.set(f'Réponse vision : {elapsed*1000:.0f} ms'); self.status.set('Conseil prêt.')
                    self.log.insert('end','🤖 ACOLYTE > '+a+'\n'); self.log.see('end')
                elif kind=='status': self.status.set(data[0])
                elif kind=='finished': self.start_button.state(['!disabled']); self.status.set('Analyse auto terminée.')
                elif kind=='result':
                    age,count,text,valid,cat,advice_count,silent_count=data; self.metric.set(f'{age*1000:.0f} ms • auto {count} • conseils {advice_count} • silence {silent_count}')
                    if valid:
                        self.advice.set(text); self.log.insert('end',f'⚡ AUTO > {text}\n'); self.log.see('end')
                        if self.speak.get() and not self.voice_busy:
                            threading.Thread(target=self.voice.speak,args=(text,lambda s:self.emit(self.session_id,'voice_status',s)),daemon=True).start()
                elif kind.startswith('update_'):
                    self.updating=False
                    if kind=='update_ok':
                        self.restart_required=True; self.status.set('Version '+data[0]+' installée.'); messagebox.showinfo('Mise à jour','Version '+data[0]+' installée. Relance Acolyte Fortnite.')
                    else:
                        self.update_button.state(['!disabled']); self.status.set('Déjà à jour.' if kind=='update_current' else 'Échec : '+data[0])
        except queue.Empty:
            pass
        self.root.after(50,self.poll)

    def stop(self):
        self.stop_event.set(); self.status.set('Arrêt de l’analyse auto…')
        if self.client: threading.Thread(target=self.client.cancel,daemon=True).start()

    def close(self):
        self.save_preferences(); self.stop_event.set(); self.voice_stop.set()
        if self.hotkey_listener:
            try: self.hotkey_listener.stop()
            except Exception: pass
        try: sd.stop()
        except Exception: pass
        self.root.destroy()

if __name__=='__main__':
    if os.name!='nt': raise SystemExit('Cette version est destinée à Windows.')
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception: pass
    root=tk.Tk(); Coach(root); root.mainloop()
