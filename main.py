from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.slider import Slider
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.popup import Popup
from kivy.uix.gridlayout import GridLayout
from kivy.clock import Clock
from kivy.core.window import Window
from android.permissions import request_permissions, Permission

from jnius import autoclass, cast, PythonJavaClass, java_method
import PyPDF2
from docx import Document
from ebooklib import epub
import mobi
import re
import json
import os
from zhconv import convert

# ---------- Android 类 ----------
PythonActivity = autoclass('org.kivy.android.PythonActivity')
TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
Locale = autoclass('java.util.Locale')
Voice = autoclass('android.speech.tts.Voice')
MediaRecorder = autoclass('android.media.MediaRecorder')
AudioSource = autoclass('android.media.MediaRecorder$AudioSource')
OutputFormat = autoclass('android.media.MediaRecorder$OutputFormat')
AudioEncoder = autoclass('android.media.MediaRecorder$AudioEncoder')

# ---------- 常量 ----------
RECORD_FILE = "read_records.json"
MP3_SAVE_DIR = "/storage/emulated/0/Download/"
CHUNK_LEN = 150

# ---------- TTS 回调类 ----------
class TTSCompletedCallback(PythonJavaClass):
    __javainterfaces__ = ['android/speech/tts/TextToSpeech$OnUtteranceCompletedListener']
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
    @java_method('(Ljava/lang/String;)V')
    def onUtteranceCompleted(self, utteranceId):
        Clock.schedule_once(lambda dt: self.callback(utteranceId), 0)

# ---------- TTS 封装 ----------
class TTSHelper:
    def __init__(self):
        self.tts = TextToSpeech(PythonActivity.mActivity, None)
        self.tts.setLanguage(Locale.CHINA)
        self.voice_list = self.get_chinese_voices()
        self.total_text = ""
        self.pos = 0
        self.is_running = False
        self.is_recording = False
        self.recorder = None
        self.timer_event = None
        self.sleep_event = None
        self.read_callback = None
        self.utterance_id = 0
        self.listener = TTSCompletedCallback(self._on_chunk_done)
        self.tts.setOnUtteranceCompletedListener(self.listener)

    def get_chinese_voices(self):
        voices = self.tts.getAvailableVoices()
        names = []
        if voices is None:
            return ["默认"]
        for v in voices.toArray():
            voice = cast(Voice, v)
            if voice.getLocale().getLanguage() == "zh":
                names.append(voice.getName())
        return names if names else ["默认"]

    def set_voice(self, voice_name):
        if voice_name == "默认": return
        for v in self.tts.getAvailableVoices().toArray():
            voice = cast(Voice, v)
            if voice.getName() == voice_name:
                self.tts.setVoice(voice)
                break

    def set_speed(self, rate): self.tts.setSpeechRate(rate)
    def set_pitch(self, pitch): self.tts.setPitch(pitch)
    def set_volume(self, vol): self.tts.setVolume(vol, vol)

    def seek_to(self, percent):
        percent = max(0, min(percent, 100))
        self.pos = int(len(self.total_text) * percent / 100)

    def prev_chunk(self): self.pos = max(0, self.pos - CHUNK_LEN)
    def next_chunk(self): self.pos = min(len(self.total_text), self.pos + CHUNK_LEN)

    def set_timer(self, minutes):
        if self.timer_event: self.timer_event.cancel()
        self.timer_event = Clock.schedule_once(lambda dt: self.stop(), minutes * 60)

    def set_sleep(self, minutes):
        if self.sleep_event: self.sleep_event.cancel()
        self.sleep_event = Clock.schedule_once(lambda dt: self.pause_read(), minutes * 60)

    def pause_read(self):
        self.is_running = False
        self.tts.stop()

    def start_read(self, text, callback):
        self.stop()
        self.total_text = text
        self.pos = 0
        self.is_running = True
        self.read_callback = callback
        self.utterance_id += 1
        self._speak_chunk()

    def _speak_chunk(self):
        if not self.is_running or self.pos >= len(self.total_text):
            self.is_running = False
            if self.read_callback: self.read_callback(100)
            return
        end = self.pos + CHUNK_LEN
        chunk = self.total_text[self.pos:end]
        params = {'utteranceId': str(self.utterance_id)}
        self.tts.speak(chunk, TextToSpeech.QUEUE_FLUSH, params)
        self.pos = end
        progress = int((self.pos / len(self.total_text)) * 100)
        if self.read_callback: self.read_callback(progress)

    def _on_chunk_done(self, utterance_id):
        if utterance_id == str(self.utterance_id):
            self._speak_chunk()

    def start_record_mp3(self, filename):
        try:
            self.recorder = MediaRecorder()
            self.recorder.setAudioSource(AudioSource.MIC)
            self.recorder.setOutputFormat(OutputFormat.MPEG_4)
            self.recorder.setAudioEncoder(AudioEncoder.AAC)
            path = MP3_SAVE_DIR + filename + "_朗读音频.m4a"
            self.recorder.setOutputFile(path)
            self.recorder.prepare()
            self.recorder.start()
            self.is_recording = True
        except Exception as e:
            print("录音启动失败", e)

    def stop_record_mp3(self):
        if self.recorder:
            try:
                self.recorder.stop()
                self.recorder.release()
            except: pass
            self.recorder = None
        self.is_recording = False

    def stop(self):
        self.is_running = False
        self.tts.stop()
        if self.timer_event: self.timer_event.cancel()
        if self.sleep_event: self.sleep_event.cancel()
        if self.is_recording: self.stop_record_mp3()

    def shutdown(self):
        self.stop()
        if self.tts: self.tts.shutdown()

# 全局实例
tts = TTSHelper()

# ---------- 文件/文本工具 ----------
def load_records():
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_record(name, path):
    recs = load_records()
    recs.insert(0, {"name": name, "path": path})
    with open(RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(recs[:10], f, ensure_ascii=False)

def clean_raw(raw):
    raw = re.sub(r'\s+', '\n', raw)
    raw = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s，。；：？！,.]', '', raw)
    return raw

def zh_switch(text, mode):
    if mode == "原文": return text
    elif mode == "繁体转简体": return convert(text, 'zh-cn')
    elif mode == "简体转繁体": return convert(text, 'zh-tw')
    return text

# ---------- 主界面 ----------
class MainLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.spacing = 4
        self.padding = 8
        self.recording = False
        self.filename = "朗读文件"
        self.raw_text_cache = ""
        self.zh_mode = "繁体转简体"

        self.add_widget(Label(text="音色"))
        self.voice_spinner = Spinner(
            text=tts.voice_list[0] if tts.voice_list else "默认",
            values=tts.voice_list if tts.voice_list else ["默认"],
            size_hint_y=None, height=38)
        self.voice_spinner.bind(text=lambda i, v: tts.set_voice(v))
        self.add_widget(self.voice_spinner)

        self.add_widget(Label(text="语速 0.5~2.0"))
        self.speed_slider = Slider(min=0.5, max=2.0, value=1.0, size_hint_y=None, height=24)
        self.speed_slider.bind(value=lambda i, v: tts.set_speed(v))
        self.add_widget(self.speed_slider)

        self.add_widget(Label(text="音调 0.8~1.5"))
        self.pitch_slider = Slider(min=0.8, max=1.5, value=1.0, size_hint_y=None, height=24)
        self.pitch_slider.bind(value=lambda i, v: tts.set_pitch(v))
        self.add_widget(self.pitch_slider)

        self.add_widget(Label(text="音量 0.0~1.0"))
        self.vol_slider = Slider(min=0.0, max=1.0, value=1.0, size_hint_y=None, height=24)
        self.vol_slider.bind(value=lambda i, v: tts.set_volume(v))
        self.add_widget(self.vol_slider)

        self.add_widget(Label(text="繁简模式"))
        self.zh_spinner = Spinner(text=self.zh_mode, values=["原文", "繁体转简体", "简体转繁体"], size_hint_y=None, height=38)
        self.zh_spinner.bind(text=self.on_zh_mode_change)
        self.add_widget(self.zh_spinner)

        row_progress = BoxLayout(spacing=4, size_hint_y=None, height=26)
        row_progress.add_widget(Label(text="进度"))
        self.progress = ProgressBar(max=100, value=0)
        row_progress.add_widget(self.progress)
        row_progress.add_widget(Button(text="跳转", size_hint_x=None, width=55, on_press=self.show_seek_pop))
        self.add_widget(row_progress)

        self.text_input = TextInput(
            hint_text="输入文字 / 打开文件朗读｜支持 TXT/PDF/Word/EPUB/MOBI",
            size_hint_y=0.40)
        self.add_widget(self.text_input)

        ctrl_box = BoxLayout(spacing=3, size_hint_y=None, height=38)
        ctrl_box.add_widget(Button(text="上一句", on_press=self.prev_chunk))
        ctrl_box.add_widget(Button(text="朗读", on_press=self.do_speak))
        ctrl_box.add_widget(Button(text="下一句", on_press=self.next_chunk))
        self.add_widget(ctrl_box)

        file_box = BoxLayout(spacing=2, size_hint_y=None, height=38)
        file_box.add_widget(Button(text="TXT", on_press=self.open_txt))
        file_box.add_widget(Button(text="PDF", on_press=self.open_pdf))
        file_box.add_widget(Button(text="Word", on_press=self.open_docx))
        file_box.add_widget(Button(text="EPUB", on_press=self.open_epub))
        file_box.add_widget(Button(text="MOBI", on_press=self.open_mobi))
        file_box.add_widget(Button(text="记录", on_press=self.show_records))
        self.add_widget(file_box)

        func_box = BoxLayout(spacing=2, size_hint_y=None, height=38)
        func_box.add_widget(Button(text="停止", on_press=self.stop_speak))
        func_box.add_widget(Button(text="后台朗读", on_press=self.enable_background))
        func_box.add_widget(Button(text="定时关闭", on_press=self.show_timer_pop))
        func_box.add_widget(Button(text="睡眠模式", on_press=self.show_sleep_pop))
        self.rec_btn = Button(text="录音导出", on_press=self.toggle_record)
        func_box.add_widget(self.rec_btn)
        self.add_widget(func_box)

        Window.bind(on_touch_down=self.on_touch_down)
        Window.bind(on_touch_move=self.on_touch_move)
        self.touch_start_x = 0

    def on_zh_mode_change(self, inst, val):
        self.zh_mode = val
        if self.raw_text_cache:
            self.text_input.text = zh_switch(self.raw_text_cache, self.zh_mode)

    def on_touch_down(self, window, touch):
        self.touch_start_x = touch.x

    def on_touch_move(self, window, touch):
        dx = touch.x - self.touch_start_x
        if abs(dx) > 40:
            if dx < 0:
                tts.pos = min(len(tts.total_text), tts.pos + 300)
            else:
                tts.pos = max(0, tts.pos - 300)
            if tts.total_text:
                self.progress.value = int(tts.pos / len(tts.total_text) * 100)
            self.touch_start_x = touch.x

    def update_progress(self, val):
        self.progress.value = val

    def prev_chunk(self, inst): tts.prev_chunk()
    def next_chunk(self, inst): tts.next_chunk()

    def show_seek_pop(self, inst):
        lay = GridLayout(cols=2, spacing=4, padding=8)
        lay.add_widget(Label(text="跳转百分比"))
        inp = TextInput(hint_text="0~100", multiline=False, size_hint_x=None, width=90)
        lay.add_widget(inp)
        pop = Popup(title="跳转", content=lay, size_hint=(0.4, 0.28))
        def confirm(instance):
            try:
                p = float(inp.text)
                tts.seek_to(p)
                if tts.total_text:
                    self.progress.value = int(tts.pos / len(tts.total_text) * 100)
            except: pass
            pop.dismiss()
        lay.add_widget(Button(text="确定", on_press=confirm))
        pop.open()

    def show_timer_pop(self, inst):
        lay = GridLayout(cols=2, spacing=4, padding=8)
        lay.add_widget(Label(text="多少分钟后关闭"))
        inp = TextInput(hint_text="分钟", multiline=False, size_hint_x=None, width=90)
        lay.add_widget(inp)
        pop = Popup(title="定时关闭", content=lay, size_hint=(0.4, 0.28))
        def confirm(instance):
            try: tts.set_timer(int(inp.text))
            except: pass
            pop.dismiss()
        lay.add_widget(Button(text="设置", on_press=confirm))
        pop.open()

    def show_sleep_pop(self, inst):
        lay = GridLayout(cols=2, spacing=4, padding=8)
        lay.add_widget(Label(text="多少分钟后暂停"))
        inp = TextInput(hint_text="分钟", multiline=False, size_hint_x=None, width=90)
        lay.add_widget(inp)
        pop = Popup(title="睡眠模式", content=lay, size_hint=(0.4, 0.28))
        def confirm(instance):
            try: tts.set_sleep(int(inp.text))
            except: pass
            pop.dismiss()
        lay.add_widget(Button(text="设置", on_press=confirm))
        pop.open()

    def enable_background(self, inst):
        PythonActivity.mActivity.moveTaskToBack(True)

    def toggle_record(self, inst):
        if not self.recording:
            tts.start_record_mp3(self.filename)
            self.rec_btn.text = "结束录音"
            self.recording = True
        else:
            tts.stop_record_mp3()
            self.rec_btn.text = "录音导出"
            self.recording = False

    def do_speak(self, inst):
        txt = self.text_input.text.strip()
        if txt: tts.start_read(txt, self.update_progress)

    def stop_speak(self, inst):
        tts.stop()
        self.progress.value = 0

    def show_chooser(self, callback):
        chooser = FileChooserListView()
        popup = Popup(title="选择文件", content=chooser, size_hint=(0.9, 0.9))
        chooser.bind(on_submit=lambda instance, sel, touch: callback(sel, popup))
        popup.open()

    def open_txt(self, inst): self.show_chooser(self.load_txt)
    def load_txt(self, sel, popup):
        popup.dismiss()
        if not sel: return
        path = sel[0]
        try:
            with open(path, 'r', encoding='utf-8') as f: raw = f.read()
            self.raw_text_cache = clean_raw(raw)
            self.text_input.text = zh_switch(self.raw_text_cache, self.zh_mode)
            self.filename = os.path.basename(path)
            save_record(self.filename, path)
        except: self.text_input.text = "TXT 读取失败"

    def open_pdf(self, inst): self.show_chooser(self.load_pdf)
    def load_pdf(self, sel, popup):
        popup.dismiss()
        if not sel: return
        path = sel[0]
        try:
            txt = ""
            with open(path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    t = page.extract_text()
                    if t: txt += t + "\n"
            self.raw_text_cache = clean_raw(txt)
            self.text_input.text = zh_switch(self.raw_text_cache, self.zh_mode)
            self.filename = os.path.basename(path)
            save_record(self.filename, path)
        except: self.text_input.text = "PDF 读取失败"

    def open_docx(self, inst): self.show_chooser(self.load_docx)
    def load_docx(self, sel, popup):
        popup.dismiss()
        if not sel: return
        path = sel[0]
        try:
            doc = Document(path)
            raw = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            self.raw_text_cache = clean_raw(raw)
            self.text_input.text = zh_switch(self.raw_text_cache, self.zh_mode)
            self.filename = os.path.basename(path)
            save_record(self.filename, path)
        except: self.text_input.text = "Word 读取失败"

    def open_epub(self, inst): self.show_chooser(self.load_epub)
    def load_epub(self, sel, popup):
        popup.dismiss()
        if not sel: return
        path = sel[0]
        try:
            book = epub.read_epub(path)
            raw = ""
            for item in book.get_items():
                if item.get_type() == epub.ITEM_DOCUMENT:
                    html = item.get_content().decode('utf-8')
                    raw += re.sub(r'<.*?>', '', html) + "\n"
            self.raw_text_cache = clean_raw(raw)
            self.text_input.text = zh_switch(self.raw_text_cache, self.zh_mode)
            self.filename = os.path.basename(path)
            save_record(self.filename, path)
        except: self.text_input.text = "EPUB 读取失败"

    def open_mobi(self, inst): self.show_chooser(self.load_mobi)
    def load_mobi(self, sel, popup):
        popup.dismiss()
        if not sel: return
        path = sel[0]
        try:
            header, raw = mobi.extract(path)
            if isinstance(raw, bytes): raw = raw.decode('utf-8', errors='ignore')
            self.raw_text_cache = clean_raw(raw)
            self.text_input.text = zh_switch(self.raw_text_cache, self.zh_mode)
            self.filename = os.path.basename(path)
            save_record(self.filename, path)
        except: self.text_input.text = "MOBI 读取失败"

    def show_records(self, inst):
        recs = load_records()
        txt = "最近记录：\n" + "\n".join(f"{i+1}. {r['name']}" for i, r in enumerate(recs)) if recs else "暂无历史记录"
        self.text_input.text = txt

# ---------- App 入口 ----------
class ReaderApp(App):
    def build(self):
        self.title = "全能朗读器"
        request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE, Permission.RECORD_AUDIO])
        return MainLayout()
    def on_stop(self):
        tts.shutdown()

if __name__ == "__main__":
    ReaderApp().run()
