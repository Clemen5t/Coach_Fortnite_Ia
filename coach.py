import base64, ctypes, io, json, os, queue, threading, time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
import updater

APP_DIR=Path(__file__).resolve().parent
DEFAULT_REPO='Clemen5t/Coach_Fortnite_Ia'
VERSION=(APP_DIR/'VERSION').read_text().strip()
import mss
from PIL import Image, ImageTk
from local_ai import LocalAI, MODEL, usable, AdviceGate

def active_game():
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(ctypes.windll.user32.GetForegroundWindow(), buf, 512)
    return 'fortnite' in buf.value.lower()

class Coach:
    def __init__(self, root):
        self.root, self.events = root, queue.Queue()
        self.stop_event = threading.Event(); self.worker = None; self.client = None; self.voice = None
        self.session_id = 0; self.updating = False; self.restart_required = False
        root.title('Coach Fortnite — IA locale — '+VERSION); root.geometry('800x750')
        root.configure(bg='#101827')
        frame = ttk.Frame(root, padding=20); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='COACH FORTNITE', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Ollama + Gemma 3 4B • analyse locale • aucune clé API').pack(anchor='w', pady=(0,14))
        self.monitor = tk.IntVar(value=1); self.interval = tk.DoubleVar(value=2.5)
        self.expiry = tk.DoubleVar(value=2.0); self.minutes = tk.IntVar(value=5)
        self.onlygame = tk.BooleanVar(value=True); self.speak = tk.BooleanVar(value=True)
        self.preferences={'monitor':self.monitor,'interval':self.interval,'expiry':self.expiry,
                          'minutes':self.minutes,'onlygame':self.onlygame,'speak':self.speak}
        try:
            saved=updater.read_json(APP_DIR/'settings.json',{})
            for name,var in self.preferences.items():
                if name in saved:var.set(saved[name])
            # Migration 0.3.4 : l'ancien réglage 1 s maintenait le GPU presque constamment occupé.
            if self.interval.get() < 2.5:self.interval.set(2.5)
        except (ValueError,TypeError,tk.TclError):pass
        grid = ttk.Frame(frame); grid.pack(fill='x')
        for row,(label,var) in enumerate([('Écran (1, 2…)',self.monitor),('Intervalle minimum (secondes)',self.interval),('Rejeter après (secondes)',self.expiry),('Arrêt automatique (minutes)',self.minutes)]):
            ttk.Label(grid,text=label).grid(row=row,column=0,sticky='w',pady=4)
            ttk.Entry(grid,textvariable=var,width=38).grid(row=row,column=1,sticky='ew',padx=10)
        ttk.Checkbutton(frame,text='Analyser uniquement quand Fortnite est au premier plan',variable=self.onlygame).pack(anchor='w',pady=5)
        ttk.Checkbutton(frame,text='Lire les conseils à voix haute',variable=self.speak).pack(anchor='w')
        ttk.Label(frame,text='Mode performance : 640×360 et 2,5 s conseillés pour limiter la perte de FPS.\nLa capture concerne tout l’écran choisi. Ferme les fenêtres privées.').pack(anchor='w',pady=8)
        buttons = ttk.Frame(frame);buttons.pack(fill='x')
        self.start_button=ttk.Button(buttons,text='Démarrer',command=self.start);self.start_button.pack(side='left')
        ttk.Button(buttons,text='Arrêter',command=self.stop).pack(side='left',padx=8)
        ttk.Button(buttons,text='Aperçu local',command=self.preview).pack(side='left')
        self.update_button=ttk.Button(buttons,text='Mettre à jour',command=self.update_app)
        self.update_button.pack(side='left',padx=8)
        ttk.Button(buttons,text='Dépôt GitHub',command=self.configure_repo).pack(side='left')
        self.status=tk.StringVar(value='Prêt. Commence par un test de 1 minute.')
        self.metric=tk.StringVar(value='Capture → réponse complète : —')
        self.advice=tk.StringVar(value='Aucun conseil')
        ttk.Label(frame,textvariable=self.status,wraplength=700).pack(anchor='w',pady=14)
        ttk.Label(frame,textvariable=self.metric).pack(anchor='w')
        ttk.Label(frame,textvariable=self.advice,font=('Segoe UI',18,'bold'),wraplength=690).pack(anchor='w',pady=15)
        self.log=tk.Text(frame,height=7,wrap='word');self.log.pack(fill='both',expand=True)
        try:
            import win32com.client
            self.voice=win32com.client.Dispatch('SAPI.SpVoice')
            for voice in self.voice.GetVoices():
                if 'french' in voice.GetDescription().lower() or 'français' in voice.GetDescription().lower():
                    self.voice.Voice=voice;break
            self.voice.Rate=2
        except Exception:
            self.status.set('Voix Windows indisponible : conseils écrits seulement.')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(50,self.poll)
    def preview(self):
        try:
            with mss.mss() as cap:
                shot=cap.grab(cap.monitors[self.monitor.get()]);im=Image.frombytes('RGB',shot.size,shot.rgb)
            im.thumbnail((960,540));window=tk.Toplevel(self.root);window.title('Aperçu local — aucune image envoyée')
            photo=ImageTk.PhotoImage(im);label=ttk.Label(window,image=photo);label.image=photo;label.pack()
        except Exception as e: messagebox.showerror('Capture',str(e))
    def start(self):
        if self.updating or self.restart_required or (self.worker and self.worker.is_alive()):return
        try:
            cfg=dict(monitor=self.monitor.get(),interval=self.interval.get(),expiry=self.expiry.get(),minutes=self.minutes.get(),onlygame=self.onlygame.get())
            if not (1.0<=cfg['interval']<=30 and 0.5<=cfg['expiry']<=10 and 1<=cfg['minutes']<=30 and cfg['monitor']>=1):raise ValueError('Intervalle : 1–30 s ; délai : 0,5–10 s ; durée : 1–30 min.')
        except Exception as e:messagebox.showerror('Réglages',str(e));return
        if cfg['interval'] < 2.5:
            if not messagebox.askyesno('Performance','Sous 2,5 s, Ollama peut faire chuter fortement les FPS de Fortnite. Continuer quand même ?'):return
        self.save_preferences()
        self.session_id+=1;self.stop_event.clear();self.start_button.state(['disabled'])
        self.worker=threading.Thread(target=self.run,args=(cfg,self.session_id),daemon=True);self.worker.start()
    def save_preferences(self):
        try:updater.write_json(APP_DIR/'settings.json',{name:var.get() for name,var in self.preferences.items()})
        except (OSError,tk.TclError):pass
    def configure_repo(self):
        if self.updating or self.restart_required:return
        try:config=updater.read_json(APP_DIR/'update-config.json',{})
        except (ValueError,OSError):config={}
        repo=simpledialog.askstring('Dépôt GitHub','Lien de ton dépôt PUBLIC Coach Fortnite :',initialvalue=config.get('repo',DEFAULT_REPO),parent=self.root)
        if not repo:return
        try:
            config={'repo':updater.normalize_repo(repo),'branch':'main'}
            updater.write_json(APP_DIR/'update-config.json',config)
            self.status.set('Dépôt configuré : '+config['repo'])
        except (ValueError,OSError) as e:messagebox.showerror('GitHub',str(e))
    def update_app(self):
        if self.updating or self.restart_required:return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Mise à jour','Arrête la session et attends sa fin avant de mettre à jour.');return
        try:config=updater.read_json(APP_DIR/'update-config.json',{})
        except (ValueError,OSError):config={}
        if not config.get('repo'):
            try:
                config={'repo':DEFAULT_REPO,'branch':'main'}
                updater.write_json(APP_DIR/'update-config.json',config)
            except OSError as e:messagebox.showerror('GitHub',str(e));return
        self.save_preferences();self.updating=True
        self.start_button.state(['disabled']);self.update_button.state(['disabled'])
        self.status.set('Recherche de mise à jour sur '+config['repo']+'…')
        def work():
            try:
                change=updater.plan(APP_DIR,config['repo'],config.get('branch','main'))
                if change is None:self.emit(self.session_id,'update_current');return
                updater.apply(APP_DIR,change)
                self.emit(self.session_id,'update_ok',change['version'])
            except Exception as e:self.emit(self.session_id,'update_error',str(e))
        threading.Thread(target=work,daemon=True).start()
    def emit(self,sid,kind,*data): self.events.put((sid,kind,data))
    def run(self,cfg,sid):
        client=LocalAI(self.stop_event);self.client=client
        try:
            self.emit(sid,'status','Vérification du modèle local…')
            client.verify()
            self.emit(sid,'status','Chargement et préchauffage de la vision…')
            data=io.BytesIO();Image.new('RGB',(640,360),'black').save(data,format='JPEG',quality=60)
            client.generate(base64.b64encode(data.getvalue()).decode(),timeout=180,warmup=True)
            if self.stop_event.is_set():return
            self.emit(sid,'status','Modèle prêt. Retourne dans Fortnite.')
            deadline=time.monotonic()+cfg['minutes']*60
            previous='';count=0;advice_count=0;silent_count=0;gate=AdviceGate()
            with mss.mss() as cap:
                if cfg['monitor']>=len(cap.monitors):raise ValueError('Numéro d’écran inexistant. Utilise Aperçu local.')
                while not self.stop_event.is_set() and time.monotonic()<deadline:
                    if cfg['onlygame'] and not active_game():
                        self.emit(sid,'status','En pause : Fortnite n’est pas au premier plan.')
                        self.stop_event.wait(.3);continue
                    started=time.monotonic();shot=cap.grab(cap.monitors[cfg['monitor']])
                    im=Image.frombytes('RGB',shot.size,shot.rgb);im.thumbnail((640,360))
                    data=io.BytesIO();im.save(data,format='JPEG',quality=62,optimize=False)
                    context=previous+'. Catégories en pause : '+', '.join(gate.blocked(time.monotonic()))
                    text=client.generate(base64.b64encode(data.getvalue()).decode(),context,timeout=min(30,max(1,deadline-time.monotonic())))
                    if self.stop_event.is_set() or time.monotonic()>=deadline:break
                    age=time.monotonic()-started;count+=1
                    foreground=not cfg['onlygame'] or active_game()
                    valid=usable(text,age,cfg['expiry'],previous) and foreground and gate.allow(client.last_category,time.monotonic())
                    if valid:advice_count+=1;previous=text
                    else:silent_count+=1
                    self.emit(sid,'result',age,count,text,valid,started,cfg['expiry'],client.last_category,client.last_evidence,advice_count,silent_count)
                    self.stop_event.wait(max(0,cfg['interval']-(time.monotonic()-started)))
        except Exception as e:
            if not self.stop_event.is_set():self.emit(sid,'status','Arrêt : '+str(e))
        finally:
            self.client=None;self.emit(sid,'finished')
    def poll(self):
        try:
            while True:
                sid,kind,data=self.events.get_nowait()
                if sid!=self.session_id:continue
                if self.stop_event.is_set() and kind!='finished' and not kind.startswith('update_'):continue
                if kind.startswith('update_'):
                    self.updating=False
                    if kind=='update_ok':
                        self.restart_required=True;self.status.set('Version '+data[0]+' installée. Ferme puis relance Coach Fortnite.')
                        messagebox.showinfo('Mise à jour installée','Ferme puis relance Coach Fortnite depuis l’icône du bureau.')
                    else:
                        self.update_button.state(['!disabled']);self.start_button.state(['!disabled'])
                        self.status.set('Déjà à jour.' if kind=='update_current' else 'Échec de mise à jour : '+data[0])
                    continue
                if kind=='status':self.status.set(data[0])
                elif kind=='finished':
                    self.start_button.state(['!disabled'])
                    if not self.status.get().startswith('Arrêt :'):self.status.set('Session terminée. Le modèle reste chargé 5 minutes dans Ollama.')
                elif kind=='result' and not self.stop_event.is_set():
                    age,count,text,valid,started,expiry,category,evidence,advice_count,silent_count=data
                    valid=valid and time.monotonic()-started<=expiry and (not self.onlygame.get() or active_game())
                    self.metric.set(f'{age*1000:.0f} ms | analyses : {count} | conseils : {advice_count} | silence : {silent_count}')
                    if age>expiry:self.status.set('IA trop lente pour le seuil choisi : conseil ignoré.')
                    elif valid:self.status.set('Conseil détecté : '+category)
                    else:self.status.set('Analyse active — aucun conseil suffisamment utile sur cette image.')
                    if valid:
                        self.advice.set(text)
                        self.log.insert('end',f'{time.strftime("%H:%M:%S")} · {age:.2f}s · {category} · {text}\n');self.log.see('end')
                        if self.voice and self.speak.get():self.voice.Speak(text,3)
        except queue.Empty:pass
        self.root.after(50,self.poll)
    def stop(self):
        self.stop_event.set();self.advice.set('En pause');self.status.set('Arrêt en cours…')
        if self.voice:self.voice.Speak('',3)
        if self.client:threading.Thread(target=self.client.cancel,daemon=True).start()
    def close(self):
        if self.updating:
            messagebox.showinfo('Mise à jour','Attends la fin de la mise à jour avant de fermer.');return
        self.save_preferences();self.stop();self.root.destroy()

if __name__=='__main__':
    if os.name!='nt':raise SystemExit('Ce prototype est destiné à Windows.')
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:pass
    root=tk.Tk();Coach(root);root.mainloop()
