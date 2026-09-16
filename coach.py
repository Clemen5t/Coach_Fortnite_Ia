import base64, ctypes, io, json, os, queue, subprocess, sys, tempfile, threading, time, wave
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
from local_ai import LocalAI, MODEL, usable, AdviceGate

VOICE_NAME='fr_FR-siwis-medium'
WHISPER_MODEL='base'

def active_game():
    buf=ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(ctypes.windll.user32.GetForegroundWindow(),buf,512)
    return 'fortnite' in buf.value.lower()

class VoiceEngine:
    def __init__(self,app_dir):
        self.root=Path(app_dir)
        self.data=self.root/'.voice';self.data.mkdir(exist_ok=True)
        self.whisper_dir=self.root/'.whisper';self.whisper_dir.mkdir(exist_ok=True)
        self.whisper=None;self.piper=None;self.lock=threading.RLock()
    @property
    def voice_model(self):return self.data/(VOICE_NAME+'.onnx')
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
            status('Chargement de Whisper local sur CPU… Le premier lancement peut télécharger le modèle.')
            from faster_whisper import WhisperModel
            self.whisper=WhisperModel(WHISPER_MODEL,device='cpu',compute_type='int8',download_root=str(self.whisper_dir))
    def prepare(self,status=lambda x:None):
        with self.lock:
            self.ensure_tts(status);self.ensure_whisper(status)
    def record_question(self,status=lambda x:None,level=lambda rms,peak,bands:None,seconds=4.5,device=None):
        with self.lock:
            self.ensure_whisper(status)
            status('🎙️ Parle maintenant…')
            rate=16000;block=512;chunks=[]
            def callback(indata,frames,timing,flags):
                samples=np.asarray(indata[:,0],dtype=np.float32).copy();chunks.append(samples)
                rms=float(np.sqrt(np.mean(samples*samples))) if len(samples) else 0.0
                peak=float(np.max(np.abs(samples))) if len(samples) else 0.0
                if len(samples):
                    window=np.hanning(len(samples));spec=np.abs(np.fft.rfft(samples*window));freq=np.fft.rfftfreq(len(samples),1/rate)
                    bands=[]
                    for lo,hi in ((80,180),(180,350),(350,700),(700,1400),(1400,2800),(2800,5000),(5000,7500)):
                        mask=(freq>=lo)&(freq<hi);value=float(np.mean(spec[mask])) if np.any(mask) else 0.0;bands.append(value)
                    m=max(bands) if bands else 0.0
                    if m>0:bands=[min(1.0,v/m) for v in bands]
                else:bands=[0.0]*7
                level(rms,peak,bands)
            try:
                with sd.InputStream(device=device,samplerate=rate,channels=1,dtype='float32',blocksize=block,callback=callback):
                    end=time.monotonic()+seconds
                    while time.monotonic()<end:time.sleep(.03)
            except Exception as e:
                raise RuntimeError('Impossible d’ouvrir le microphone sélectionné : '+str(e)) from e
            level(0.0,0.0,[0.0]*7)
            samples=np.concatenate(chunks) if chunks else np.zeros(0,dtype=np.float32)
            if samples.size==0:raise RuntimeError('Aucun échantillon reçu du microphone sélectionné.')
            status('Transcription locale…')
            segments,_=self.whisper.transcribe(samples,language='fr',beam_size=1,best_of=1,
                vad_filter=True,condition_on_previous_text=False,temperature=0)
            return ' '.join(s.text.strip() for s in segments if s.text.strip()).strip()
    def speak(self,text,status=lambda x:None):
        if not text:return
        with self.lock:
            self.ensure_tts(status);status('🔊 Acolyte répond…')
            fd,path=tempfile.mkstemp(suffix='.wav');os.close(fd)
            try:
                with wave.open(path,'wb') as wav_file:self.piper.synthesize_wav(text,wav_file)
                with wave.open(path,'rb') as wav_file:
                    rate=wav_file.getframerate();channels=wav_file.getnchannels();width=wav_file.getsampwidth();raw=wav_file.readframes(wav_file.getnframes())
                if width==2:data=np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768.0
                elif width==4:data=np.frombuffer(raw,dtype=np.int32).astype(np.float32)/2147483648.0
                else:raise RuntimeError('Format audio Piper non pris en charge.')
                if channels>1:data=data.reshape(-1,channels)
                sd.play(data,rate);sd.wait()
            finally:
                try:os.unlink(path)
                except OSError:pass

class Coach:
    def __init__(self,root):
        self.root,self.events=root,queue.Queue();self.stop_event=threading.Event();self.voice_stop=threading.Event()
        self.worker=None;self.client=None;self.session_id=0;self.updating=False;self.restart_required=False
        self.voice_busy=False;self.voice=VoiceEngine(APP_DIR);self.history=[];self.hotkey_listener=None
        try:self.saved_settings=updater.read_json(APP_DIR/'settings.json',{}) or {}
        except Exception:self.saved_settings={}
        root.title('Coach Fortnite — Acolyte local — '+VERSION);root.geometry('900x900');root.configure(bg='#101827')
        frame=ttk.Frame(root,padding=20);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='ACOLYTE FORTNITE',font=('Segoe UI',23,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Whisper + Gemma 3 Vision + Piper • 100 % local après installation • aucune clé API').pack(anchor='w',pady=(0,12))

        voicebox=ttk.LabelFrame(frame,text='Acolyte vocal — mode conseillé',padding=12);voicebox.pack(fill='x',pady=(0,12))
        ttk.Label(voicebox,text='Appuie sur F8 en jeu ou clique sur le bouton, pose ta question, puis Acolyte regarde l’écran et te répond.').pack(anchor='w')

        microw=ttk.Frame(voicebox);microw.pack(fill='x',pady=(8,2))
        ttk.Label(microw,text='Microphone :').pack(side='left')
        self.mic_choice=tk.StringVar(value=str(self.saved_settings.get('microphone','')))
        self.mic_combo=ttk.Combobox(microw,textvariable=self.mic_choice,state='readonly',width=62)
        self.mic_combo.pack(side='left',fill='x',expand=True,padx=(8,6))
        ttk.Button(microw,text='Actualiser',command=self.refresh_microphones).pack(side='left')
        self.mic_devices={}
        self.mic_combo.bind('<<ComboboxSelected>>',lambda e:self.on_microphone_changed())
        self.refresh_microphones(initial=True)

        row=ttk.Frame(voicebox);row.pack(fill='x',pady=(6,0))
        self.talk_button=ttk.Button(row,text='🎙 Parler au coach (F8)',command=self.ask_voice);self.talk_button.pack(side='left')
        ttk.Button(row,text='Préparer la voix IA',command=self.prepare_voice).pack(side='left',padx=8)
        self.voice_state=tk.StringVar(value='Prêt. F8 = parler au coach.')
        ttk.Label(voicebox,textvariable=self.voice_state,wraplength=800).pack(anchor='w',pady=(8,3))
        meterrow=ttk.Frame(voicebox);meterrow.pack(fill='x',pady=(3,0))
        self.mic_label=tk.StringVar(value='Micro : en attente')
        ttk.Label(meterrow,textvariable=self.mic_label,width=28).pack(side='left')
        self.meter=tk.Canvas(meterrow,height=34,width=450,highlightthickness=1,highlightbackground='#777777',bg='#151515')
        self.meter.pack(side='left',fill='x',expand=True,padx=(6,0))
        self.audio_bars=[]
        for i in range(20):
            x1=5+i*21;x2=x1+14
            self.audio_bars.append(self.meter.create_rectangle(x1,27,x2,29,fill='#454545',outline=''))

        autobox=ttk.LabelFrame(frame,text='Analyse automatique — optionnelle',padding=10);autobox.pack(fill='x')
        self.monitor=tk.IntVar(value=1);self.interval=tk.DoubleVar(value=4.0);self.expiry=tk.DoubleVar(value=2.5);self.minutes=tk.IntVar(value=5)
        self.onlygame=tk.BooleanVar(value=True);self.speak=tk.BooleanVar(value=True)
        self.preferences={'monitor':self.monitor,'interval':self.interval,'expiry':self.expiry,'minutes':self.minutes,'onlygame':self.onlygame,'speak':self.speak,'microphone':self.mic_choice}
        try:
            for name,var in self.preferences.items():
                if name in self.saved_settings and name!='microphone':var.set(self.saved_settings[name])
            if self.interval.get()<3:self.interval.set(4.0)
        except (ValueError,TypeError,tk.TclError):pass
        grid=ttk.Frame(autobox);grid.pack(fill='x')
        for r,(label,var) in enumerate([('Écran (1, 2…)',self.monitor),('Intervalle auto (secondes)',self.interval),('Rejeter après (secondes)',self.expiry),('Durée auto (minutes)',self.minutes)]):
            ttk.Label(grid,text=label).grid(row=r,column=0,sticky='w',pady=3);ttk.Entry(grid,textvariable=var,width=28).grid(row=r,column=1,sticky='w',padx=10)
        ttk.Checkbutton(autobox,text='Analyser seulement quand Fortnite est au premier plan',variable=self.onlygame).pack(anchor='w',pady=3)
        ttk.Checkbutton(autobox,text='Lire aussi les conseils automatiques avec Piper',variable=self.speak).pack(anchor='w')
        autorow=ttk.Frame(autobox);autorow.pack(fill='x',pady=(6,0))
        self.start_button=ttk.Button(autorow,text='Démarrer auto',command=self.start);self.start_button.pack(side='left')
        ttk.Button(autorow,text='Arrêter auto',command=self.stop).pack(side='left',padx=8)
        ttk.Button(autorow,text='Aperçu local',command=self.preview).pack(side='left')

        tools=ttk.Frame(frame);tools.pack(fill='x',pady=10)
        self.update_button=ttk.Button(tools,text='Mettre à jour',command=self.update_app);self.update_button.pack(side='left')
        ttk.Button(tools,text='Dépôt GitHub',command=self.configure_repo).pack(side='left',padx=8)
        self.status=tk.StringVar(value='Mode vocal prêt. L’analyse vision ne tourne pas tant que tu ne demandes rien.')
        self.metric=tk.StringVar(value='Aucune analyse en cours.')
        self.advice=tk.StringVar(value='Dis « Coach… » dans ta question après avoir appuyé sur F8, ou pose directement ta question.')
        ttk.Label(frame,textvariable=self.status,wraplength=830).pack(anchor='w',pady=(4,8))
        ttk.Label(frame,textvariable=self.metric).pack(anchor='w')
        ttk.Label(frame,textvariable=self.advice,font=('Segoe UI',17,'bold'),wraplength=830).pack(anchor='w',pady=10)
        self.log=tk.Text(frame,height=9,wrap='word');self.log.pack(fill='both',expand=True)
        self.install_hotkey();root.protocol('WM_DELETE_WINDOW',self.close);root.after(50,self.poll)

    def refresh_microphones(self,initial=False):
        previous=self.mic_choice.get().strip()
        try:
            devices=sd.query_devices();default_input=None
            try:
                default_input=sd.default.device[0]
                if default_input is not None:default_input=int(default_input)
            except Exception:default_input=None
            values=[];mapping={}
            for index,dev in enumerate(devices):
                if int(dev.get('max_input_channels',0) or 0)<=0:continue
                name=str(dev.get('name','Microphone')).strip()
                host=''
                try:host=str(sd.query_hostapis(int(dev.get('hostapi',0))).get('name','')).strip()
                except Exception:pass
                label=f'{index} — {name}' + (f' [{host}]' if host else '')
                values.append(label);mapping[label]=index
            self.mic_devices=mapping;self.mic_combo['values']=values
            selected=''
            if previous in mapping:selected=previous
            elif previous:
                old_name=previous.split(' — ',1)[-1].split(' [',1)[0].strip().lower()
                for label in values:
                    if label.split(' — ',1)[-1].split(' [',1)[0].strip().lower()==old_name:
                        selected=label;break
            if not selected and default_input is not None:
                selected=next((label for label,idx in mapping.items() if idx==default_input),'')
            if not selected and values:selected=values[0]
            self.mic_choice.set(selected)
            if not initial:self.save_preferences()
            if selected:self.mic_label.set('Micro sélectionné : '+selected.split(' — ',1)[-1][:45])
            else:self.mic_label.set('Micro : aucun périphérique d’entrée')
        except Exception as e:
            self.mic_devices={};self.mic_combo['values']=();self.mic_choice.set('');self.mic_label.set('Micro : erreur de détection');
            if not initial:messagebox.showerror('Microphone','Impossible de lister les microphones : '+str(e))
    def selected_microphone(self):
        label=self.mic_choice.get().strip()
        if label not in self.mic_devices:self.refresh_microphones(initial=True);label=self.mic_choice.get().strip()
        if label not in self.mic_devices:raise RuntimeError('Aucun microphone valide sélectionné.')
        return self.mic_devices[label]
    def on_microphone_changed(self):
        self.save_preferences();label=self.mic_choice.get().strip()
        if label:self.mic_label.set('Micro sélectionné : '+label.split(' — ',1)[-1][:45])

    def install_hotkey(self):
        try:
            from pynput import keyboard
            def on_press(key):
                if key==keyboard.Key.f8:self.root.after(0,self.ask_voice)
            self.hotkey_listener=keyboard.Listener(on_press=on_press);self.hotkey_listener.daemon=True;self.hotkey_listener.start()
        except Exception as e:self.voice_state.set('F8 indisponible : utilise le bouton Parler au coach. '+str(e))
    def emit(self,sid,kind,*data):self.events.put((sid,kind,data))
    def capture_for_ai(self):
        with mss.mss() as cap:
            monitor=self.monitor.get()
            if monitor<1 or monitor>=len(cap.monitors):raise ValueError('Numéro d’écran inexistant. Utilise Aperçu local.')
            shot=cap.grab(cap.monitors[monitor]);im=Image.frombytes('RGB',shot.size,shot.rgb);im.thumbnail((768,432))
            data=io.BytesIO();im.save(data,format='JPEG',quality=68,optimize=False)
            return base64.b64encode(data.getvalue()).decode()
    def prepare_voice(self):
        if self.voice_busy:return
        self.voice_busy=True;self.talk_button.state(['disabled']);sid=self.session_id
        def work():
            try:self.voice.prepare(lambda s:self.emit(sid,'voice_status',s));self.emit(sid,'voice_ready')
            except Exception as e:self.emit(sid,'voice_error',str(e))
        threading.Thread(target=work,daemon=True).start()
    def ask_voice(self):
        if self.voice_busy or self.updating:return
        try:mic_device=self.selected_microphone()
        except Exception as e:messagebox.showerror('Microphone',str(e));return
        self.voice_busy=True;self.talk_button.state(['disabled']);sid=self.session_id
        def work():
            try:
                question=self.voice.record_question(lambda s:self.emit(sid,'voice_status',s),lambda rms,peak,bands:self.emit(sid,'audio_level',rms,peak,bands),device=mic_device)
                if not question:
                    self.emit(sid,'voice_error','Je n’ai pas détecté de phrase. Vérifie le spectre et le microphone sélectionné.');return
                self.emit(sid,'question',question);self.emit(sid,'voice_status','📸 Capture de Fortnite et analyse…')
                image=self.capture_for_ai();ai=LocalAI(self.voice_stop);ai.verify()
                hist='\n'.join(f'Joueur: {q}\nAcolyte: {a}' for q,a in self.history[-4:])
                started=time.monotonic();answer=ai.ask(image,question,hist,timeout=40);elapsed=time.monotonic()-started
                self.history.append((question,answer));self.history=self.history[-6:]
                self.emit(sid,'answer',question,answer,elapsed)
                self.voice.speak(answer,lambda s:self.emit(sid,'voice_status',s));self.emit(sid,'voice_ready')
            except Exception as e:self.emit(sid,'voice_error',str(e))
        threading.Thread(target=work,daemon=True).start()
    def preview(self):
        try:
            with mss.mss() as cap:
                shot=cap.grab(cap.monitors[self.monitor.get()]);im=Image.frombytes('RGB',shot.size,shot.rgb)
            im.thumbnail((960,540));window=tk.Toplevel(self.root);window.title('Aperçu local')
            photo=ImageTk.PhotoImage(im);label=ttk.Label(window,image=photo);label.image=photo;label.pack()
        except Exception as e:messagebox.showerror('Capture',str(e))
    def start(self):
        if self.updating or self.restart_required or (self.worker and self.worker.is_alive()):return
        try:
            cfg=dict(monitor=self.monitor.get(),interval=self.interval.get(),expiry=self.expiry.get(),minutes=self.minutes.get(),onlygame=self.onlygame.get())
            if not (2<=cfg['interval']<=30 and .5<=cfg['expiry']<=10 and 1<=cfg['minutes']<=30 and cfg['monitor']>=1):raise ValueError('Intervalle : 2–30 s ; délai : 0,5–10 s ; durée : 1–30 min.')
        except Exception as e:messagebox.showerror('Réglages',str(e));return
        self.save_preferences();self.session_id+=1;self.stop_event.clear();self.start_button.state(['disabled'])
        self.worker=threading.Thread(target=self.run_auto,args=(cfg,self.session_id),daemon=True);self.worker.start()
    def run_auto(self,cfg,sid):
        client=LocalAI(self.stop_event);self.client=client
        try:
            self.emit(sid,'status','Vérification du modèle local…');client.verify();deadline=time.monotonic()+cfg['minutes']*60
            previous='';count=advice_count=silent_count=0;gate=AdviceGate()
            while not self.stop_event.is_set() and time.monotonic()<deadline:
                if cfg['onlygame'] and not active_game():self.emit(sid,'status','Auto en pause : Fortnite n’est pas au premier plan.');self.stop_event.wait(.5);continue
                started=time.monotonic();image=self.capture_for_ai();context=previous+'. Catégories en pause : '+', '.join(gate.blocked(time.monotonic()))
                text=client.generate(image,context,timeout=min(30,max(1,deadline-time.monotonic())))
                if self.stop_event.is_set():break
                age=time.monotonic()-started;count+=1;valid=usable(text,age,cfg['expiry'],previous) and gate.allow(client.last_category,time.monotonic())
                if valid:advice_count+=1;previous=text
                else:silent_count+=1
                self.emit(sid,'result',age,count,text,valid,client.last_category,advice_count,silent_count)
                self.stop_event.wait(max(0,cfg['interval']-(time.monotonic()-started)))
        except Exception as e:
            if not self.stop_event.is_set():self.emit(sid,'status','Arrêt : '+str(e))
        finally:self.client=None;self.emit(sid,'finished')
    def save_preferences(self):
        try:updater.write_json(APP_DIR/'settings.json',{name:var.get() for name,var in self.preferences.items()})
        except (OSError,tk.TclError,AttributeError):pass
    def configure_repo(self):
        if self.updating:return
        try:config=updater.read_json(APP_DIR/'update-config.json',{})
        except (ValueError,OSError):config={}
        repo=simpledialog.askstring('Dépôt GitHub','Lien du dépôt PUBLIC :',initialvalue=config.get('repo',DEFAULT_REPO),parent=self.root)
        if not repo:return
        try:config={'repo':updater.normalize_repo(repo),'branch':'main'};updater.write_json(APP_DIR/'update-config.json',config);self.status.set('Dépôt configuré : '+config['repo'])
        except (ValueError,OSError) as e:messagebox.showerror('GitHub',str(e))
    def update_app(self):
        if self.updating or self.voice_busy:return
        if self.worker and self.worker.is_alive():messagebox.showinfo('Mise à jour','Arrête d’abord l’analyse automatique.');return
        try:config=updater.read_json(APP_DIR/'update-config.json',{}) or {'repo':DEFAULT_REPO,'branch':'main'}
        except Exception:config={'repo':DEFAULT_REPO,'branch':'main'}
        if not config.get('repo'):config={'repo':DEFAULT_REPO,'branch':'main'}
        try:updater.write_json(APP_DIR/'update-config.json',config)
        except OSError:pass
        self.save_preferences();self.updating=True;self.update_button.state(['disabled']);self.status.set('Recherche de mise à jour…');sid=self.session_id
        def work():
            try:
                change=updater.plan(APP_DIR,config['repo'],config.get('branch','main'))
                if change is None:self.emit(sid,'update_current');return
                updater.apply(APP_DIR,change);self.emit(sid,'update_ok',change['version'])
            except Exception as e:self.emit(sid,'update_error',str(e))
        threading.Thread(target=work,daemon=True).start()
    def draw_audio(self,rms,peak,bands):
        level=min(1.0,max(0.0,rms*12.0));active=int(round(level*20))
        for i,item in enumerate(self.audio_bars):
            height=3
            if i<active:
                band=bands[min(len(bands)-1,int(i*len(bands)/20))] if bands else 0.0
                height=5+int(22*max(level,band*.75));fill='#45d483' if peak<.85 else '#ff9f43'
            else:fill='#454545'
            x1=5+i*21;x2=x1+14;self.meter.coords(item,x1,30-height,x2,29);self.meter.itemconfigure(item,fill=fill)
        if peak>=.98:self.mic_label.set('Micro : saturation')
        elif rms>.025:self.mic_label.set('Micro : voix détectée')
        elif rms>.004:self.mic_label.set('Micro : signal faible')
        else:self.mic_label.set('Micro : silence')
    def poll(self):
        try:
            while True:
                sid,kind,data=self.events.get_nowait()
                if sid!=self.session_id and not kind.startswith('update_'):continue
                if kind=='audio_level':self.draw_audio(*data)
                elif kind=='voice_status':self.voice_state.set(data[0])
                elif kind=='voice_ready':
                    self.voice_busy=False;self.talk_button.state(['!disabled']);self.voice_state.set('Prêt. F8 = parler au coach.');self.draw_audio(0,0,[0]*7)
                    label=self.mic_choice.get().strip();self.mic_label.set('Micro sélectionné : '+label.split(' — ',1)[-1][:45] if label else 'Micro : en attente')
                elif kind=='voice_error':
                    self.voice_busy=False;self.talk_button.state(['!disabled']);self.voice_state.set('Erreur : '+data[0]);self.status.set('Acolyte vocal indisponible.');self.draw_audio(0,0,[0]*7)
                elif kind=='question':self.log.insert('end','\n🎙️ Toi : '+data[0]+'\n');self.log.see('end')
                elif kind=='answer':
                    q,a,elapsed=data;self.advice.set(a);self.metric.set(f'Question → réponse vision : {elapsed*1000:.0f} ms');self.status.set('Acolyte a répondu à ta question.')
                    self.log.insert('end','🤖 Acolyte : '+a+'\n');self.log.see('end')
                elif kind.startswith('update_'):
                    self.updating=False
                    if kind=='update_ok':
                        self.restart_required=True;self.status.set('Version '+data[0]+' installée. Ferme puis relance Coach Fortnite.');messagebox.showinfo('Mise à jour','Version '+data[0]+' installée. Relance depuis l’icône du bureau.')
                    else:self.update_button.state(['!disabled']);self.status.set('Déjà à jour.' if kind=='update_current' else 'Échec de mise à jour : '+data[0])
                elif kind=='status':self.status.set(data[0])
                elif kind=='finished':self.start_button.state(['!disabled']);self.status.set('Analyse automatique terminée. Le mode vocal reste disponible.')
                elif kind=='result':
                    age,count,text,valid,category,advice_count,silent_count=data;self.metric.set(f'{age*1000:.0f} ms | auto : {count} | conseils : {advice_count} | silence : {silent_count}')
                    if valid:
                        self.advice.set(text);self.log.insert('end',f'{time.strftime("%H:%M:%S")} · AUTO · {text}\n');self.log.see('end')
                        if self.speak.get() and not self.voice_busy:threading.Thread(target=self.voice.speak,args=(text,lambda s:self.emit(self.session_id,'voice_status',s)),daemon=True).start()
        except queue.Empty:pass
        self.root.after(50,self.poll)
    def stop(self):
        self.stop_event.set();self.status.set('Arrêt de l’analyse automatique…')
        if self.client:threading.Thread(target=self.client.cancel,daemon=True).start()
    def close(self):
        if self.updating:messagebox.showinfo('Mise à jour','Attends la fin de la mise à jour.');return
        self.save_preferences();self.stop_event.set();self.voice_stop.set()
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
    root=tk.Tk();Coach(root);root.mainloop()
