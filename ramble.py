"""Ramble: hold a key, talk through your thoughts, get organized text typed where you're working.

Hold Right Ctrl and talk, then let go. Double-tap Right Ctrl for hands-free and tap it again
to finish. While recording, tap Shift to change the mode (Edit comes first when text is
selected) and press Esc to cancel. Ctrl+Alt+L pastes the last result again.

Groq turns speech into text (Whisper) and rewrites it for the app you're in (an open model),
both on Groq's free plan. If Groq can't be reached, the optional offline mode (Whisper on this
PC, installed by "Install offline mode.bat") turns speech into text and the text is only lightly
cleaned.
"""

import collections
import ctypes
import ctypes.wintypes as wt
import importlib
import importlib.util
import io
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import wave
import winsound
from contextlib import contextmanager
from dataclasses import dataclass, field

# UI Automation clients work best in a multithreaded COM apartment; this must be set before
# comtypes is first imported (it happens lazily, in the threads that use UI Automation).
sys.coinit_flags = 0

import groq  # noqa: E402
import numpy as np  # noqa: E402
import pystray  # noqa: E402
import pywintypes  # noqa: E402
import sounddevice as sd  # noqa: E402
import win32clipboard  # noqa: E402
import win32con  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pynput import keyboard  # noqa: E402

APP_NAME = "Ramble"
# Not under AppData: Windows redirects AppData writes from packaged apps, which would split
# settings between a copy started from Explorer and one started by another app.
DATA_DIR = os.path.join(os.path.expanduser("~"), ".ramble")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
MODES_PATH = os.path.join(DATA_DIR, "modes.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
USAGE_PATH = os.path.join(DATA_DIR, "usage.json")
USAGE_REPORT_PATH = os.path.join(DATA_DIR, "usage.txt")
LOG_PATH = os.path.join(DATA_DIR, "ramble.log")
STARTUP_LINK = os.path.join(
    os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup\Ramble.lnk"
)

DEFAULT_SETTINGS = {
    "groq_api_key": "",
    "hotkey": "ctrl_r",  # hold to talk, double-tap for hands-free
    "mode_key": "shift",  # tap while recording to change the mode
    "paste_last_hotkey": "ctrl+alt+l",
    "llm_model": "openai/gpt-oss-120b",
    "stt_model": "whisper-large-v3-turbo",
    "language": None,  # None = detect; or "en", "ar", ...
    "microphone": None,  # None = Windows default
    "keep_mic_warm": True,
    "sounds": True,
    "llm_timeout_seconds": 5,
    "local_whisper_model": "small",  # used when Groq can't be reached
    "output_language": "same",  # same | english | franco | arabic | french
    "fallback_llm_model": "openai/gpt-oss-20b",  # used when the main model's daily limit is used up
    "daily_token_limit": 200000,  # Groq free plan, per model (console.groq.com > Settings > Limits)
    "daily_request_limit": 1000,
    "dictionary": [],
    "snippets": {},
}

DEFAULT_MODES = {
    "default_mode": "clean",
    "browsers": ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe", "zen.exe"],
    "app_rules": [
        {"mode": "email", "apps": ["outlook.exe", "olk.exe", "thunderbird.exe"], "browser_titles": ["gmail", "outlook"]},
        {"mode": "slides", "apps": ["powerpnt.exe"], "browser_titles": ["google slides"]},
        {
            "mode": "message",
            "apps": ["slack.exe", "ms-teams.exe", "teams.exe", "whatsapp.exe", "whatsapp.root.exe", "discord.exe",
                     "telegram.exe", "signal.exe"],
            "browser_titles": ["slack", "microsoft teams", "whatsapp", "discord", "telegram"],
        },
        {
            "mode": "prompt",
            "apps": ["claude.exe", "chatgpt.exe", "cursor.exe", "code.exe", "code - insiders.exe", "windsurf.exe",
                     "devenv.exe", "idea64.exe", "pycharm64.exe", "windowsterminal.exe", "cmd.exe", "powershell.exe",
                     "pwsh.exe", "wezterm-gui.exe", "alacritty.exe"],
            "browser_titles": ["chatgpt", "claude", "gemini", "copilot", "perplexity", "grok", "deepseek", "mistral",
                               "notebooklm", "cursor"],
        },
    ],
    "modes": {
        "clean": {
            "label": "Clean",
            "context": ["field"],
            "instructions": [
                "Light clean-up only: keep their wording and sentence order.",
                "Don't restructure, summarize, add to or change the tone of what they said.",
            ],
        },
        "prompt": {
            "label": "AI prompt",
            # Not "field": text already in the chat box (often the last prompt) got merged into new ones.
            "context": ["clipboard"],
            "required": "Goal:",  # a reply without this is asked for again once
            "instructions": [
                "Turn what they said into a well-structured prompt for an AI assistant (coding, research, writing "
                "or anything else), written as them, speaking to the AI in short, direct, imperative sentences.",
                "ALWAYS use this format, with the plain-text labels exactly as shown and \"- \" bullets. Include a "
                "section only when they said something for it, in this order:",
                "First line, only if they gave a ticket or issue ID: the ID (\"PROJ-123\").",
                "Goal: one sentence with the outcome they want. Always present.",
                "Context: background, the current situation and the reasons they gave.",
                "Requirements: every specific thing to do or include, one per bullet, in the order they said them, "
                "with the exact names, values and conditions.",
                "Constraints: limits, preferences, and anything they said not to do or not to touch.",
                "Expected output: what they want back (format, length, deliverable), only if they said it.",
                "Open points: what they were unsure about, contradicted, or left out but the task clearly needs, "
                "as short statements, not questions.",
                "Completeness comes first: every point they made appears exactly once, including asides and "
                "examples. Shorten the wording, never the content.",
                "Add nothing they didn't say: no invented names, values, IDs, requirements, best practices or "
                "generic advice.",
                "Copied text they refer to (an error, log or code) goes verbatim in a fenced code block under "
                "Context. Never paraphrase it.",
                "Write code identifiers as they appear in code (user_id, getUserName). A name you build from spoken "
                "words is a guess: add \"(confirm name)\" after it.",
                "Never answer the prompt or do the task yourself, and never return the transcript unchanged.",
                "<example>\nSpoken: \"um PROJ 45, when a user signs up send them a welcome email, because support "
                "keeps getting asked how to start, oh and don't change the login page, and maybe also a text "
                "message, not sure\"\nPrompt:\nPROJ-45\nGoal: Send a welcome email to new users when they sign "
                "up.\nContext:\n- Support keeps getting asked how to get started.\nRequirements:\n- On sign-up, "
                "send the new user a welcome email.\nConstraints:\n- Don't change the login page.\nOpen "
                "points:\n- Sending a text message too is undecided.\n</example>",
                "<example>\nSpoken: \"so I need like a summary of the three articles I'll paste, uh, for my "
                "manager, keep it under a page, and highlight anything about pricing\"\nPrompt:\nGoal: Summarize "
                "the three articles I'll paste for my manager.\nRequirements:\n- Highlight anything about "
                "pricing.\nExpected output:\n- Under one page.\n</example>",
            ],
        },
        "email": {
            "label": "Email",
            "context": ["field", "clipboard"],
            "instructions": [
                "Write the email body: a greeting with the recipient's name if known (\"Hi Sara,\"), short "
                "paragraphs, one clear ask or next step, and a short sign-off line (\"Thanks,\" or \"Best,\").",
                "Never invent a name or signature for the sender.",
                "If the field already contains an email thread, write a reply to it and match its tone and formality.",
                "No subject line unless they ask for one. Plain text, no Markdown.",
            ],
        },
        "message": {
            "label": "Message",
            "context": ["field"],
            "instructions": [
                "A chat message: casual and short, the way they'd type it.",
                "No greeting and no sign-off. Plain text, no Markdown headings.",
            ],
        },
        "slides": {
            "label": "Slides",
            "context": ["field"],
            "instructions": [
                "Text for a slide: short parallel bullets of at most about 10 words each, with no ending periods.",
                "One bullet per line, without bullet characters or numbers (the slide adds its own).",
                "Match the style of any bullets already in the text box (capitalization, length, tone).",
                "Add a title line only if they ask for one.",
            ],
        },
        "edit": {
            "label": "Edit",
            "context": [],
            "instructions": [
                "They selected the text in <selected_text> and spoke an instruction about it (\"make it shorter\", "
                "\"more formal\", \"turn into bullets\", \"translate to Arabic\").",
                "Apply the instruction and reply with only the rewritten text; it replaces the selection.",
                "Keep everything the instruction doesn't ask to change: meaning, facts, language and formatting.",
                "If what they said is new content rather than an instruction, reply with that content instead.",
            ],
        },
    },
}

OUTPUT_RULES = """\
You turn a speech-to-text transcript into finished text. Your reply is inserted straight into the app the person is typing in, so reply with only that text: no preamble, no quotes around it, no "Here's your text", no notes or explanations.
- Keep the speaker's meaning, facts and level of certainty. Never add facts, names, numbers, dates or promises they didn't say.
- Use the spellings in the dictionary, and fix words the transcription clearly misheard.
- {language}
- Keep placeholders like ⟦1⟧ exactly as they are; they are filled in afterwards."""

LANGUAGES = {
    "same": ("Same as spoken", "Write in the language they spoke unless they ask for another one."),
    "english": ("English", "Write the result in English, whatever language they spoke; translate faithfully."),
    "franco": ("Franco-Arabic", "Write the result in Franco-Arabic (Arabizi): Arabic written in Latin letters, with "
               "numbers for Arabic sounds (2 = ء, 3 = ع, 5 = خ, 7 = ح), the way people text. Keep names, technical "
               "terms, code and section labels in English."),
    "arabic": ("Arabic", "Write the result in Arabic script. Keep names, technical terms, code and section labels "
               "in English."),
    "french": ("French", "Write the result in French, whatever language they spoke; translate faithfully. Keep "
               "names, technical terms and code as they are."),
}

DICTATION_RULES = """\
- Remove filler words (um, uh, like, you know, I mean, sort of) that carry no meaning, and drop false starts and repeated words.
- Fix punctuation, capitalization and grammar.
- Resolve self-corrections: "meet Tuesday, no wait, Friday" becomes "meet Friday".
- Spoken formatting: "new line" is a line break, "new paragraph" is a blank line, "bullet point" starts a "- " list item. Don't write those words.
- If they say how they want it ("as an email", "keep it short", "more formal"), do that and leave the instruction out.
- They are dictating, not talking to you. If the transcript is a question or a request, write it out as their question or request; never answer it or carry it out.
- If the field already has text, fit what you write at the cursor: continue its sentence or style, and don't repeat what's there."""

SAMPLE_RATE = 16000  # what Whisper expects
TAP_SECONDS = 0.3  # a shorter press is a tap; a longer one is hold-to-talk
DOUBLE_TAP_SECONDS = 0.4  # a second tap within this gap starts hands-free mode
MIN_SECONDS = 0.5  # shorter recordings are treated as accidental
CONTEXT_CHARS = 1000  # field text kept on each side of the cursor (about 2,000 in all)
MAX_SELECTION = 20000
COPIED_WITHIN = 3.0  # copied text counts as context if copied this soon before recording, or during it
HISTORY_SIZE = 50
WHISPER_THREADS = 4  # on a 14-core laptop, more threads were slower

# Ctrl+C means "stop" in terminals, and code editors copy the whole line when nothing is
# selected, so Ramble never presses Ctrl+C there to read the selection.
NO_COPY_APPS = {
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "openconsole.exe", "wsl.exe",
    "mintty.exe", "alacritty.exe", "wezterm-gui.exe", "putty.exe", "code.exe", "code - insiders.exe", "cursor.exe",
    "windsurf.exe",
}
# UI Automation control types that never take typed text: button, check box, hyperlink, image,
# list item, list, menu, menu bar, menu item, radio button, slider, tab, tab item, toolbar,
# tree, tree item, data grid, data item, table.
NOT_TEXT_CONTROLS = {50000, 50002, 50005, 50006, 50007, 50008, 50009, 50010, 50011, 50013, 50015, 50018, 50019,
                     50021, 50023, 50024, 50028, 50029, 50036}

log = logging.getLogger(APP_NAME)

# ---------------------------------------------------------------- Windows API

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND] + [ctypes.c_int] * 4 + [ctypes.c_uint]
user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_uint]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SendMessageTimeoutW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM, ctypes.c_uint, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x8000000
SW_HIDE, SW_RESTORE = 0, 9
HWND_TOPMOST = wt.HWND(-1)
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x10, 0x40
SPI_GETWORKAREA = 0x30
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10
VK_C, VK_V, VK_ESCAPE = 0x43, 0x56, 0x1B
EM_GETSEL, SMTO_ABORTIFHUNG = 0xB0, 0x2
MODIFIER_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}


def message_box(text, error=False):
    user32.MessageBoxW(None, text, APP_NAME, 0x10 if error else 0x40)


def already_running():
    kernel32.CreateMutexW(None, False, "Local\\RambleDictation")
    return ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def window_info(hwnd):
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        path = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            process = os.path.basename(path.value).lower()
        kernel32.CloseHandle(handle)
    return process, title.value


def edit_selection(hwnd):
    """(start, end) of the selection in a classic Windows edit box, or None for any other control."""
    if not hwnd:
        return None
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    if "edit" not in name.value.lower():  # Edit, RichEdit20W, WindowsForms10.EDIT...
        return None
    start, end, result = wt.DWORD(), wt.DWORD(), ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(hwnd, EM_GETSEL, ctypes.addressof(start), ctypes.addressof(end),
                                      SMTO_ABORTIFHUNG, 500, ctypes.byref(result)):
        return None
    return start.value, end.value


def focus_window(hwnd):
    """Bring a window back to the front. Windows only allows this to the app that has the
    foreground, so Ramble briefly shares its input state."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    ours = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    attached = bool(theirs) and theirs != ours and user32.AttachThreadInput(ours, theirs, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)
    for attempt in range(2):
        for _ in range(20):
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.01)
        if attempt == 0:
            user32.SwitchToThisWindow(hwnd, True)  # what Alt+Tab uses; allowed in more cases
    return False


# ---------------------------------------------------------------- settings


def load_json(path, defaults):
    """Read a settings file, writing back any defaults it's missing (and dropping unknown keys)."""
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    merged = {key: data.get(key, value) for key, value in defaults.items()}
    if merged != data:
        save_json(path, merged)
    return merged


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def open_in_notepad(path):
    if not os.path.exists(path):
        open(path, "a", encoding="utf-8").close()
    subprocess.Popen(["notepad.exe", path])


def resolve_key(name):
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        raise ValueError(f'Unknown key "{name}" in settings. Try ctrl_r, alt_r, f9 or caps_lock.') from None


def key_family(name):
    """The key plus its left and right versions, so "shift" matches either Shift key."""
    keys = {resolve_key(name)}
    for side in ("_l", "_r"):
        if hasattr(keyboard.Key, name + side):
            keys.add(getattr(keyboard.Key, name + side))
    return keys


def key_label(name):
    labels = {"ctrl_r": "Right Ctrl", "alt_r": "Right Alt", "shift_r": "Right Shift", "caps_lock": "Caps Lock",
              "scroll_lock": "Scroll Lock", "menu": "Menu key"}
    return labels.get(name, name.replace("_", " ").title())


def parse_combo(text):
    """"ctrl+alt+l" -> ({"ctrl", "alt"}, virtual-key code of L)."""
    *mods, key = [part.strip().lower() for part in text.split("+")]
    if set(mods) - MODIFIER_VKS.keys() or len(key) != 1 or not key.isalnum():
        raise ValueError(f'Can\'t read the hotkey "{text}" in settings. Use something like ctrl+alt+l.')
    return set(mods), ord(key.upper())


def modifiers_held():
    return {name for name, vks in MODIFIER_VKS.items() if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)}


def detect_mode(process, title, modes):
    """The mode for an app: by process name first, then by keywords in a browser tab's title."""
    for rule in modes["app_rules"]:
        if process in rule.get("apps", []):
            return rule["mode"]
    if process in modes["browsers"]:
        title = title.lower()
        for rule in modes["app_rules"]:
            if any(word in title for word in rule.get("browser_titles", [])):
                return rule["mode"]
    return modes["default_mode"]


# ---------------------------------------------------------------- clipboard and keys

CLIPBOARD_FORMATS = [win32con.CF_UNICODETEXT, win32con.CF_DIB] + [
    win32clipboard.RegisterClipboardFormat(name) for name in ("HTML Format", "Rich Text Format", "PNG")
]


@contextmanager
def open_clipboard():
    for _ in range(50):  # another app may be holding it for a moment
        try:
            win32clipboard.OpenClipboard()
            break
        except pywintypes.error:
            time.sleep(0.02)
    else:
        raise RuntimeError("the clipboard is busy")
    try:
        yield
    finally:
        win32clipboard.CloseClipboard()


def save_clipboard():
    """Copy the clipboard's text, rich text and image so it can be put back afterwards."""
    saved = []
    with open_clipboard():
        for fmt in CLIPBOARD_FORMATS:
            if win32clipboard.IsClipboardFormatAvailable(fmt):
                try:
                    saved.append((fmt, win32clipboard.GetClipboardData(fmt)))
                except pywintypes.error:
                    pass
    return saved


def restore_clipboard(saved):
    with open_clipboard():
        win32clipboard.EmptyClipboard()
        for fmt, data in saved:
            try:
                win32clipboard.SetClipboardData(fmt, data)
            except pywintypes.error:
                pass


def set_clipboard_text(text):
    with open_clipboard():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)


def get_clipboard_text():
    with open_clipboard():
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    return None


class ClipboardWatch:
    """Notes when the user last copied something, so a fresh copy can be used as context."""

    def __init__(self):
        self.last_copy = 0.0
        self._lock = threading.Lock()
        self._seq = user32.GetClipboardSequenceNumber()
        threading.Thread(target=self._run, daemon=True).start()

    @contextmanager
    def ours(self):
        """Wrap Ramble's own clipboard use (one at a time) so it doesn't count as the user copying."""
        with self._lock:
            try:
                yield
            finally:
                self._seq = user32.GetClipboardSequenceNumber()

    def _run(self):
        while True:
            time.sleep(0.15)
            with self._lock:
                seq = user32.GetClipboardSequenceNumber()
                if seq != self._seq:
                    self._seq = seq
                    self.last_copy = time.monotonic()


def press_ctrl(kb, vk):
    """Send Ctrl+<key> by virtual-key code, so it works with any keyboard layout (e.g. Arabic)."""
    key = keyboard.KeyCode.from_vk(vk)
    with kb.pressed(keyboard.Key.ctrl):
        kb.press(key)
        kb.release(key)


def copy_selection(kb):
    """Press Ctrl+C and return (whether the clipboard changed, the copied text)."""
    before = user32.GetClipboardSequenceNumber()
    press_ctrl(kb, VK_C)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        time.sleep(0.02)
        if user32.GetClipboardSequenceNumber() != before:
            time.sleep(0.05)  # let the app finish putting all its formats on the clipboard
            return True, get_clipboard_text() or ""
    return False, ""


def type_text(kb, text, hwnd):
    """Type text key by key, for fields that block pasting. Stops (returning False) if another window
    comes to the front, so the rest can't land in the wrong app. Line breaks use Shift+Enter so chat
    apps don't send the message early."""
    for i, line in enumerate(text.split("\n")):
        if i:
            if user32.GetForegroundWindow() != hwnd:
                return False
            with kb.pressed(keyboard.Key.shift):
                kb.press(keyboard.Key.enter)
                kb.release(keyboard.Key.enter)
        for start in range(0, len(line), 20):
            if user32.GetForegroundWindow() != hwnd:
                return False
            kb.type(line[start:start + 20])
    return True


# ---------------------------------------------------------------- UI Automation


@dataclass
class Field:
    """The focused control when recording started, as UI Automation sees it."""

    element: object = None
    kind: str = "unknown"  # text | none | password | unknown
    editable: bool = False  # safe to type into: an edit box, or a value that isn't read-only
    readable: bool = False  # exposes its text, so Ramble can check that a paste landed
    knows_selection: bool = False  # the cursor and selection could be read without copying
    selection: str = ""
    before: str = ""  # text just before the cursor (or the selection)
    after: str = ""  # text just after it


class Uia:
    """Windows UI Automation: what the focused control is, its text around the cursor, and refocusing it."""

    api = None  # the generated UIAutomationClient module

    def __init__(self):
        self._local = threading.local()

    def client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            import comtypes
            import comtypes.client

            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient

            Uia.api = UIAutomationClient
            client = comtypes.client.CreateObject(
                UIAutomationClient.CUIAutomation8, interface=UIAutomationClient.IUIAutomation2
            )
            client.ConnectionTimeout = 1000  # don't hang on an app that stopped responding
            client.TransactionTimeout = 1000
            self._local.client = client
        return client

    def _pattern(self, element, pattern_id, interface):
        try:
            unknown = element.GetCurrentPattern(pattern_id)
            return unknown.QueryInterface(interface) if unknown else None
        except Exception:
            return None

    def focused_field(self):
        """Describe the focused control. Never raises: when UI Automation can't tell, the kind is "unknown"."""
        try:
            element = self.client().GetFocusedElement()
        except Exception:
            log.warning("UI Automation is unavailable", exc_info=True)
            return Field()
        api, found = self.api, Field(element=element)
        try:
            if element.CurrentIsPassword:
                found.kind = "password"
                return found
            text = self._pattern(element, api.UIA_TextPatternId, api.IUIAutomationTextPattern)
            value = self._pattern(element, api.UIA_ValuePatternId, api.IUIAutomationValuePattern)
            control = element.CurrentControlType
            value_editable = value is not None and not value.CurrentIsReadOnly
            found.editable = value_editable or control == api.UIA_EditControlTypeId
            found.readable = text is not None or value is not None
            if control in NOT_TEXT_CONTROLS and not value_editable and text is None:
                found.kind = "none"
                return found
            found.kind = "text"
            if text is not None:
                found.knows_selection = True
                found.selection, found.before, found.after = self._around_selection(text, CONTEXT_CHARS)
            elif value is not None:
                content = value.CurrentValue
                span = edit_selection(element.CurrentNativeWindowHandle)
                if span is None:
                    found.before = content[-CONTEXT_CHARS:]  # where the cursor is is unknown; assume the end
                else:
                    start, end = span
                    found.knows_selection = True
                    found.selection = content[start:end]
                    found.before = content[max(0, start - CONTEXT_CHARS):start]
                    found.after = content[end:end + CONTEXT_CHARS]
        except Exception:
            log.warning("Couldn't read the focused field", exc_info=True)
        return found

    def _around_selection(self, text, chars):
        api = self.api
        ranges = text.GetSelection()
        if not ranges or ranges.Length == 0:
            return "", "", ""
        selection = ranges.GetElement(0)
        before = selection.Clone()
        before.MoveEndpointByRange(api.TextPatternRangeEndpoint_End, selection, api.TextPatternRangeEndpoint_Start)
        before.MoveEndpointByUnit(api.TextPatternRangeEndpoint_Start, api.TextUnit_Character, -chars)
        after = selection.Clone()
        after.MoveEndpointByRange(api.TextPatternRangeEndpoint_Start, selection, api.TextPatternRangeEndpoint_End)
        after.MoveEndpointByUnit(api.TextPatternRangeEndpoint_End, api.TextUnit_Character, chars)
        return selection.GetText(MAX_SELECTION), before.GetText(chars), after.GetText(chars)

    def snapshot(self, element, chars=100):
        """A quick fingerprint of the field's text, to tell when a paste lands. None if the field hides its text."""
        try:
            self.client()
            api = self.api
            text = self._pattern(element, api.UIA_TextPatternId, api.IUIAutomationTextPattern)
            if text is not None:
                return "\x00".join(self._around_selection(text, chars))
            value = self._pattern(element, api.UIA_ValuePatternId, api.IUIAutomationValuePattern)
            if value is not None:
                content = value.CurrentValue
                return f"{len(content)}\x00{content[-chars:]}"
        except Exception:
            pass
        return None

    def ensure_focus(self, element):
        try:
            client = self.client()
            if not client.CompareElements(client.GetFocusedElement(), element):
                element.SetFocus()
        except Exception:
            log.warning("Couldn't refocus the field", exc_info=True)


# ---------------------------------------------------------------- text without AI

FILLER_RE = re.compile(r"(?<![\w'])(?:u+m+|u+h+|e+r+m*|a+h+|h+m+|m+h*m+)(?![\w'])[,.]?\s*", re.IGNORECASE)
SPOKEN_COMMANDS = {"new line": "\n", "new paragraph": "\n\n", "bullet point": "\n- "}
MODE_WORDS = {
    "clean": "clean", "clean text": "clean", "prompt": "prompt", "ai prompt": "prompt", "email": "email",
    "e-mail": "email", "message": "message", "chat message": "message", "text message": "message",
    "slide": "slides", "slides": "slides", "slide text": "slides", "bullets": "slides",
}
# "As an email: ..." / "Write this as a prompt, ..." at the very start picks the mode. The
# punctuation after the mode word is required, so "as an email client..." doesn't count.
PREFIX_RE = re.compile(
    r"^\s*(?:as|make (?:it|this)|write (?:it|this) as|format (?:it|this) as)\s+(?:an?\s+)?("
    + "|".join(sorted(map(re.escape, MODE_WORDS), key=len, reverse=True))
    + r")\s*[:,.\-]+\s*",
    re.IGNORECASE,
)


def light_clean(text):
    """Quick clean-up without AI: drop um/uh-style fillers and tidy spacing and capitals."""
    text = FILLER_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text).strip()
    return text[:1].upper() + text[1:]


def spoken_mode(transcript):
    """("As an email: hi Sara...") -> ("email", "Hi Sara..."); (None, transcript) when no mode is named."""
    match = PREFIX_RE.match(transcript)
    if not match:
        return None, transcript
    rest = transcript[match.end():]
    return MODE_WORDS[match.group(1).lower()], rest[:1].upper() + rest[1:]


def add_placeholders(transcript, snippets):
    """Swap spoken snippet triggers for ⟦1⟧-style placeholders. Returns the new transcript and {placeholder: text}."""
    found = {}
    for number, (trigger, text) in enumerate(snippets.items(), 1):
        words = re.findall(r"\w+", trigger)
        if not words:
            continue
        pattern = re.compile(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", re.IGNORECASE)
        if pattern.search(transcript):
            token = f"⟦{number}⟧"
            transcript = pattern.sub(token, transcript)
            found[token] = text
    return transcript, found


def fill_placeholders(text, found):
    for token, value in found.items():
        text = text.replace(token, value)
    return text


def direct_output(transcript, found):
    """Text that needs no AI: a spoken command on its own ("new line") or a snippet trigger on its own."""
    bare = re.sub(r"[\s.,!?]+", " ", transcript).strip().lower()
    if bare in SPOKEN_COMMANDS:
        return SPOKEN_COMMANDS[bare]
    return found.get(bare)


def fit_at_cursor(text, before):
    """Add a space when the cursor sits right after a word, so the new text doesn't run into it."""
    if before and not before[-1].isspace() and text[:1].isalnum():
        return " " + text
    return text


def has_speech(audio):
    """True if some 50 ms stretches are clearly louder than the background (Whisper invents text from silence)."""
    frames = audio[: len(audio) // 800 * 800].reshape(-1, 800)
    if not len(frames):
        return False
    loudness = np.sqrt((frames**2).mean(axis=1))
    floor = np.percentile(loudness, 10)
    return int((loudness > max(0.003, floor * 3)).sum()) >= 5  # at least a quarter second


# ---------------------------------------------------------------- audio and speech-to-text


class Recorder:
    """The microphone. When kept warm the input stream stays open, so recording starts instantly and
    includes a moment from just before the key press."""

    PREROLL_SECONDS = 0.3

    def __init__(self, device, warm):
        self.device, self.warm = device, warm
        self.stream = None
        self.rate = SAMPLE_RATE
        self.recording = False
        self.chunks = []
        self.preroll = collections.deque()
        self.levels = collections.deque(maxlen=8)  # recent loudness, for the level meter
        self.lock = threading.Lock()
        if warm:
            try:
                self._open()
            except Exception:
                log.exception("Couldn't keep the microphone open; it will open on each recording")
                self.warm = False

    def _open(self):
        try:
            self.stream = self._make(SAMPLE_RATE)
        except sd.PortAudioError:
            # Some mics can't record at 16 kHz; record at their own rate and resample in stop().
            self.stream = self._make(int(sd.query_devices(self.device, "input")["default_samplerate"]))
        self.stream.start()

    def _make(self, rate):
        self.rate = rate
        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=self.device,
                              blocksize=int(rate * 0.05), callback=self._callback)

    def _callback(self, data, frames, when, status):
        mono = data[:, 0].copy()
        self.levels.append(float(np.sqrt(np.mean(mono**2))))
        with self.lock:
            if self.recording:
                self.chunks.append(mono)
            else:
                self.preroll.append(mono)
                while len(self.preroll) * len(mono) > self.PREROLL_SECONDS * self.rate:
                    self.preroll.popleft()

    def _close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except sd.PortAudioError:
                pass
            self.stream = None

    def start(self):
        if self.stream is None or not self.stream.active:
            self._close()  # a warm stream can die when a headset is unplugged
            self._open()
        with self.lock:
            self.chunks = list(self.preroll)
            self.preroll.clear()
            self.recording = True

    def stop(self):
        with self.lock:
            self.recording = False
            chunks, self.chunks = self.chunks, []
        if not self.warm:
            self._close()
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if self.rate != SAMPLE_RATE and len(audio):
            n = int(len(audio) * SAMPLE_RATE / self.rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio


def wav_bytes(audio):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


AV_REQUIREMENT = "av==19.0.2"  # Opus audio compression, about half of the install size


def install_audio_compression():
    """Fetch the audio compressor in the background on first run, which keeps setup short. Until it arrives,
    audio is sent as WAV (bigger, same result)."""
    if importlib.util.find_spec("av") is not None:
        return
    log.info("Installing the audio compressor in the background")
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")  # pythonw has no console to print to
    try:
        subprocess.run(
            [python if os.path.exists(python) else sys.executable, "-m", "pip", "install",
             "--disable-pip-version-check", "-q", AV_REQUIREMENT],
            capture_output=True, timeout=900, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        importlib.invalidate_caches()  # so the running app can import it right away
        log.info("Audio compressor installed")
    except Exception:
        log.warning("Couldn't install the audio compressor; audio is sent uncompressed", exc_info=True)


def encode_audio(audio):
    """Opus in Ogg: about a tenth the size of WAV, so it uploads fast. Falls back to WAV."""
    if importlib.util.find_spec("av") is None:
        return "speech.wav", wav_bytes(audio)
    try:
        import av

        buf = io.BytesIO()
        with av.open(buf, "w", format="ogg") as container:
            stream = container.add_stream("libopus", rate=SAMPLE_RATE)
            stream.bit_rate = 32000
            samples = (np.clip(audio, -1, 1) * 32767).astype(np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = SAMPLE_RATE
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        return "speech.ogg", buf.getvalue()
    except Exception:
        log.exception("Couldn't compress the audio; sending WAV")
        return "speech.wav", wav_bytes(audio)


class OfflineUnavailable(Exception):
    """Groq failed and Whisper on this PC isn't installed."""


class SpeechToText:
    """Groq's Whisper, with Whisper on this PC as an optional fallback (installed separately, loaded when first needed)."""

    def __init__(self):
        self._local = None
        self._lock = threading.Lock()

    def transcribe(self, audio, client, settings):
        """Returns (text, engine), where engine is "groq" or "local"."""
        hint = ", ".join(settings["dictionary"])[:800]  # Whisper's prompt is limited to 224 tokens
        if client is not None:
            try:
                name, data = encode_audio(audio)
                options = {"prompt": hint} if hint else {}
                if settings["language"]:
                    options["language"] = settings["language"]
                result = client.audio.transcriptions.create(
                    file=(name, data), model=settings["stt_model"], temperature=0.0, timeout=10.0, **options
                )
                return result.text.strip(), "groq"
            except Exception as e:
                log.warning("Groq speech-to-text failed (%s); using Whisper on this PC", e)
        return self._transcribe_locally(audio, hint, settings), "local"

    def _transcribe_locally(self, audio, hint, settings):
        with self._lock:
            if self._local is None:
                os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
                try:  # optional: installed by "Install offline mode.bat"
                    from faster_whisper import WhisperModel
                except ImportError:
                    raise OfflineUnavailable(
                        "Can't reach Groq, and offline speech isn't installed. "
                        "Run Install offline mode.bat once (needs internet)."
                    ) from None

                name = settings["local_whisper_model"]
                options = dict(device="cpu", compute_type="int8", cpu_threads=WHISPER_THREADS)
                try:  # works offline once the model has been downloaded
                    self._local = WhisperModel(name, local_files_only=True, **options)
                except Exception:
                    self._local = WhisperModel(name, **options)
            segments, _ = self._local.transcribe(
                audio, language=settings["language"] or None, vad_filter=True, beam_size=1,
                without_timestamps=True, initial_prompt=hint or None,
            )
            return " ".join(s.text.strip() for s in segments).strip()


def build_prompt(mode_key, mode, target, transcript, selection="", clipboard="", dictionary=(), language="same"):
    """The system and user messages for the clean-up call."""
    language_rule = LANGUAGES.get(language, LANGUAGES["same"])[1]
    rules = [OUTPUT_RULES.format(language=language_rule)] + ([] if mode_key == "edit" else [DICTATION_RULES])
    instructions = "\n".join(f"- {line}" for line in mode["instructions"])
    system = "\n\n".join(rules + [f"Mode: {mode['label']}\n{instructions}"])

    wants = mode.get("context", [])
    parts = [f'App: {target.process or "unknown"}, window "{target.title}"']
    if "field" in wants and (target.field.before or target.field.after):
        parts.append(
            "Text already in the field (⟨CURSOR⟩ marks where your text goes):\n<field>\n"
            f"{target.field.before}⟨CURSOR⟩{target.field.after}\n</field>"
        )
    if "clipboard" in wants and clipboard:
        parts.append(f"Text they copied just before speaking:\n<clipboard>\n{clipboard}\n</clipboard>")
    if dictionary:
        parts.append("Dictionary: " + ", ".join(dictionary))
    if mode_key == "edit":
        parts.append(f"<selected_text>\n{selection}\n</selected_text>")
        parts.append(f"<instruction>\n{transcript}\n</instruction>")
    else:
        parts.append(f"<transcript>\n{transcript}\n</transcript>")
    if language != "same":  # restated last, where the model pays most attention
        parts.append(language_rule)
    return system, "\n\n".join(parts)


PROMPT_LABELS = ("Goal:", "Context:", "Requirements:", "Constraints:", "Expected output:", "Open points:")


def tidy_prompt(text):
    """Same layout every time: drop sections the model left empty and put one blank line between sections."""
    blocks, current = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.rstrip()
        if line.replace("**", "").lstrip("#* ").startswith(PROMPT_LABELS):
            line = line.replace("**", "").lstrip("#* ")
            if current:
                blocks.append(current)
            current = [line]
        elif line or (current and current[-1].startswith("```")) or any(l.startswith("```") for l in current):
            current.append(line)
    if current:
        blocks.append(current)
    filler = re.compile(r"^[-*\s]*(none( specified| mentioned| given)?|n/?a|not specified)\.?$", re.IGNORECASE)
    kept = [b for b in blocks if not (b[0] in PROMPT_LABELS
                                      and not any(l.strip() and not filler.match(l) for l in b[1:]))]
    return "\n\n".join("\n".join(b).strip() for b in kept)


def _call_model(client, settings, model, messages):
    extra = {"reasoning_effort": "low", "include_reasoning": False} if model.startswith("openai/gpt-oss") else {}
    response = client.chat.completions.create(
        model=model, messages=messages, temperature=0.3, max_completion_tokens=4096,
        timeout=float(settings["llm_timeout_seconds"]), **extra,
    )
    text = (response.choices[0].message.content or "").strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":  # the model sometimes wraps it in quotes
        text = text[1:-1].strip()
    return text, response.usage.total_tokens if response.usage else 0


def rewrite(client, settings, system, user, required="", usage=None):
    """The clean-up call. Moves to the fallback model when the main one's daily limit is used up, and asks once
    more when a reply is empty or lacks the mode's required label. Returns (text, model); raises groq.APIError."""
    models = [settings["llm_model"]]
    if settings["fallback_llm_model"] and settings["fallback_llm_model"] != models[0]:
        models.append(settings["fallback_llm_model"])
    if usage is not None and len(models) > 1 and usage.used_up(models[0], settings):
        models = models[1:]  # don't waste a call that's sure to be refused
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for position, model in enumerate(models):
        try:
            text, tokens = _call_model(client, settings, model, messages)
            if usage is not None:
                usage.add_call(model, tokens)
            if not text or (required and required not in text):
                log.info("Reply from %s was empty or off-format; asking again", model)
                retry = messages + [{"role": "assistant", "content": text or "(empty)"}, {
                    "role": "user",
                    "content": "That reply didn't follow your instructions. Rewrite the transcript exactly as "
                               "instructed" + (f", using the format that includes \"{required}\"." if required else "."),
                }]
                text, tokens = _call_model(client, settings, model, retry)
                if usage is not None:
                    usage.add_call(model, tokens)
            if not text:
                raise RuntimeError("the model returned no text")
            return text, model
        except groq.RateLimitError as e:
            if position == len(models) - 1:
                raise
            log.warning("%s hit a limit (%s); using %s", model, e, models[position + 1])
            if usage is not None and "per day" in str(e):  # a per-minute limit clears by itself in seconds
                usage.mark_used_up(model)


class Usage:
    """Daily counts in usage.json, for the limit meter, the model fallback and the stats report."""

    def __init__(self):
        self._lock = threading.Lock()
        try:
            with open(USAGE_PATH, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("days", {})

    def _today(self):
        day = self.data["days"].setdefault(time.strftime("%Y-%m-%d"), {})
        for key in ("tokens", "requests", "modes", "used_up"):
            day.setdefault(key, {})
        for key in ("dictations", "speech_seconds", "words", "fallbacks", "warned"):
            day.setdefault(key, 0)
        return day

    def _save(self):
        save_json(USAGE_PATH, self.data)

    def add_call(self, model, tokens):
        with self._lock:
            day = self._today()
            day["tokens"][model] = day["tokens"].get(model, 0) + tokens
            day["requests"][model] = day["requests"].get(model, 0) + 1
            self._save()

    def mark_used_up(self, model):
        with self._lock:
            self._today()["used_up"][model] = True
            self._save()

    def used_up(self, model, settings):
        with self._lock:
            day = self._today()
            return bool(day["used_up"].get(model)) or (
                day["tokens"].get(model, 0) >= settings["daily_token_limit"] * 0.98
                or day["requests"].get(model, 0) >= settings["daily_request_limit"])

    def add_dictation(self, mode, seconds, words, model, settings):
        with self._lock:
            day = self._today()
            day["dictations"] += 1
            day["speech_seconds"] = round(day["speech_seconds"] + seconds, 1)
            day["words"] += words
            day["modes"][mode] = day["modes"].get(mode, 0) + 1
            if model and model != settings["llm_model"]:
                day["fallbacks"] += 1
            self._save()

    def share_used(self, settings):
        """How much of the main model's daily token limit is used, from 0 to 1."""
        with self._lock:
            return self._today()["tokens"].get(settings["llm_model"], 0) / settings["daily_token_limit"]

    def should_warn(self, settings):
        """True once a day, when 80% of the main model's daily limit is used."""
        with self._lock:
            day = self._today()
            used = day["tokens"].get(settings["llm_model"], 0) / settings["daily_token_limit"]
            if used >= 0.8 and not day["warned"]:
                day["warned"] = 1
                self._save()
                return True
            return False

    def meter(self, settings):
        with self._lock:
            day = self._today()
            used = day["tokens"].get(settings["llm_model"], 0)
            note = ", using backup model" if day["used_up"].get(settings["llm_model"]) else ""
            return (f"Today: {day['dictations']} dictations, {used / 1000:.0f}K of "
                    f"{settings['daily_token_limit'] // 1000}K tokens ({used / settings['daily_token_limit']:.0%}){note}")

    def report(self, settings):
        with self._lock:
            days = self.data["days"]
            today = time.strftime("%Y-%m-%d")
            week = [time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400 * n)) for n in range(7)]

            def total(keys):
                picked = [days[k] for k in keys if k in days]
                minutes = sum(d.get("speech_seconds", 0) for d in picked) / 60
                words = sum(d.get("words", 0) for d in picked)
                modes = collections.Counter()
                for d in picked:
                    modes.update(d.get("modes", {}))
                saved = max(0.0, words / 40 - minutes)  # typing at about 40 words a minute, minus talking time
                return (f"  Dictations: {sum(d.get('dictations', 0) for d in picked)}\n"
                        f"  Minutes spoken: {minutes:.0f}\n  Words written: {words}\n"
                        f"  Time saved vs typing (40 wpm): about {saved:.0f} min\n"
                        f"  Backup model used: {sum(d.get('fallbacks', 0) for d in picked)} times\n"
                        f"  Modes: {', '.join(f'{k} {v}' for k, v in modes.most_common()) or 'none'}")

            tokens = "\n".join(f"  {model}: {count:,} of {settings['daily_token_limit']:,} tokens, "
                               f"{days.get(today, {}).get('requests', {}).get(model, 0)} of "
                               f"{settings['daily_request_limit']:,} requests"
                               for model, count in days.get(today, {}).get("tokens", {}).items()) or "  none yet"
            return (f"Ramble usage ({time.strftime('%Y-%m-%d %H:%M')})\n\nToday\n{total([today])}\n\nGroq today\n"
                    f"{tokens}\n\nLast 7 days\n{total(week)}\n\nAll time\n{total(list(days))}\n")


def describe_error(e):
    if isinstance(e, groq.AuthenticationError):
        return "Groq rejected the API key"
    if isinstance(e, groq.RateLimitError):
        return "Groq's free limit is used up for now"
    if isinstance(e, groq.APITimeoutError):
        return "Groq took too long"
    if isinstance(e, groq.APIConnectionError):
        return "can't reach Groq"
    if isinstance(e, groq.APIStatusError):
        return f"Groq error {e.status_code}"
    return "the AI step failed"


# ---------------------------------------------------------------- history and sounds


def add_history(entry):
    lines = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    lines = (lines + [json.dumps(entry, ensure_ascii=False)])[-HISTORY_SIZE:]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def last_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def make_sounds():
    """Two short soft blips for start and stop, generated once."""
    for name, (start_hz, end_hz) in {"start": (660, 990), "stop": (880, 520)}.items():
        path = os.path.join(DATA_DIR, f"{name}.wav")
        if os.path.exists(path):
            continue
        rate, n = 44100, int(44100 * 0.08)
        tone = np.sin(2 * np.pi * np.cumsum(np.linspace(start_hz, end_hz, n)) / rate) * np.hanning(n) * 0.2
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((tone * 32767).astype("<i2").tobytes())


def play_sound(name):
    winsound.PlaySound(os.path.join(DATA_DIR, f"{name}.wav"),
                       winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


# ---------------------------------------------------------------- on-screen status

BG, TEXT, HINT = "#1f1f24", "#ececef", "#9a9aa3"
COLORS = {"listen": "#ff6b6b", "work": "#7fb2ff", "ok": "#6fdc9d", "warn": "#ffbf5f", "info": "#9a9aa3"}


class Overlay:
    """A small always-on-top status pill that never takes focus from the app you're typing in."""

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.94)
        self.win.configure(bg=BG)
        self.top = tk.Frame(self.win, bg=BG)
        self.top.pack(padx=16, pady=9, anchor="w")
        self.icon = tk.Canvas(self.top, width=12, height=12, bg=BG, highlightthickness=0)
        self.icon.pack(side="left", padx=(0, 9))
        self.title = tk.Label(self.top, bg=BG, fg=TEXT, font=("Segoe UI", 11))
        self.title.pack(side="left")
        self.meter = tk.Canvas(self.top, width=46, height=16, bg=BG, highlightthickness=0)
        self.hint = tk.Label(self.win, bg=BG, fg=HINT, font=("Segoe UI", 9))
        self.win.geometry("+-3000+-3000")
        self.win.update_idletasks()
        self.hwnd = user32.GetParent(self.win.winfo_id())
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOPMOST
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
        user32.ShowWindow(self.hwnd, SW_HIDE)
        self._hide_job = None
        self._tick_job = None

    def show(self, text, color="info", seconds=None, hint=""):
        self._cancel_jobs()
        self._render(text, color, hint)
        if seconds:
            self._hide_job = self.win.after(int(seconds * 1000), self.hide)

    def show_live(self, read, color):
        """Keep refreshing from read() -> (text, hint, levels) while recording."""
        self._cancel_jobs()

        def tick():
            text, hint, levels = read()
            self._render(text, color, hint, levels)
            self._tick_job = self.win.after(100, tick)

        tick()

    def hide(self):
        self._cancel_jobs()
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def _cancel_jobs(self):
        for job in (self._hide_job, self._tick_job):
            if job:
                self.win.after_cancel(job)
        self._hide_job = self._tick_job = None

    def _render(self, text, color, hint="", levels=None):
        self.icon.delete("all")
        self.icon.create_oval(1, 1, 11, 11, fill=COLORS[color], outline="")
        self.title.config(text=text)
        if levels is None:
            self.meter.pack_forget()
        else:
            self.meter.pack(side="left", padx=(12, 0))
            self.meter.delete("all")
            for i, rms in enumerate(list(levels)[-8:]):
                db = 20 * np.log10(rms + 1e-9)
                height = 2 + 12 * float(np.clip((db + 60) / 45, 0, 1))  # -60 dB silent ... -15 dB loud
                self.meter.create_rectangle(i * 6, 16 - height, i * 6 + 4, 16, fill=COLORS[color], outline="")
        if hint:
            self.hint.config(text=hint)
            self.top.pack_configure(pady=(9, 0))
            self.hint.pack(padx=16, pady=(2, 9), anchor="w")
        else:
            self.hint.pack_forget()
            self.top.pack_configure(pady=9)
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        area = wt.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        x = area.left + (area.right - area.left - w) // 2
        y = area.bottom - h - 28
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.update_idletasks()
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)


def tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=(88, 86, 214))
    draw.rounded_rectangle((24, 12, 40, 38), radius=8, fill="white")
    draw.arc((16, 20, 48, 46), start=0, end=180, fill="white", width=4)
    draw.line((32, 46, 32, 53), fill="white", width=4)
    draw.line((24, 53, 40, 53), fill="white", width=4)
    return img


def toggle_startup():
    if os.path.exists(STARTUP_LINK):
        os.remove(STARTUP_LINK)
        return
    import pythoncom
    from win32com.client import Dispatch

    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    except pywintypes.com_error:
        pass  # this thread already has COM
    here = os.path.dirname(os.path.abspath(__file__))
    link = Dispatch("WScript.Shell").CreateShortCut(STARTUP_LINK)
    link.TargetPath = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    link.Arguments = f'"{os.path.join(here, "ramble.py")}"'
    link.WorkingDirectory = here
    link.Save()


# ---------------------------------------------------------------- the app


@dataclass
class Target:
    """Where the text goes: locked in when recording starts."""

    hwnd: int
    process: str
    title: str
    field: Field = field(default_factory=Field)


@dataclass
class Job:
    audio: object
    target: Target
    mode: str
    auto_mode: str
    started: float


class App:
    def __init__(self):
        self.settings = load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
        self.modes = load_json(MODES_PATH, DEFAULT_MODES)
        self.hotkey = resolve_key(self.settings["hotkey"])
        self.key_name = key_label(self.settings["hotkey"])
        self.mode_keys = key_family(self.settings["mode_key"])
        self.mode_key_name = key_label(self.settings["mode_key"])
        self.paste_mods, self.paste_vk = parse_combo(self.settings["paste_last_hotkey"])
        self.recorder = Recorder(self.settings["microphone"], self.settings["keep_mic_warm"])
        self.stt = SpeechToText()
        self.uia = Uia()
        self.clipwatch = ClipboardWatch()
        self.usage = Usage()
        self.tray = None
        self.kb = keyboard.Controller()
        self.events = queue.Queue()  # key events from the keyboard hook
        self.jobs = queue.Queue()  # work for the worker thread
        self.calls = queue.Queue()  # functions to run on the Tk (UI) thread
        self._groq, self._groq_key = None, None
        # Recording state, owned by the control thread.
        self.state = "idle"  # idle | holding | tapped | handsfree
        self.started = 0.0
        self.deadline = None  # when the current tap or hold decision is due
        self.confirmed = False  # the hold has lasted long enough to show the pill
        self.target = None
        self.mode = self.auto_mode = self.modes["default_mode"]
        # Keyboard hook state.
        self.hotkey_down = False
        self.swallowed = set()  # keys whose key-up Ramble must also hide from the app

    def run(self):
        make_sounds()
        threading.Thread(target=self.uia.client, daemon=True).start()  # prepare UI Automation early
        threading.Thread(target=install_audio_compression, daemon=True).start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.overlay = Overlay(self.root)
        self.root.after(40, self._run_calls)
        self.listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release, win32_event_filter=self._filter
        )
        self.listener.start()
        threading.Thread(target=self._control_loop, daemon=True).start()
        threading.Thread(target=self._work_loop, daemon=True).start()
        self.tray = self._make_tray()
        threading.Thread(target=self.tray.run, daemon=True).start()
        if self._groq_api_key():
            self.overlay.show(f"Ramble is on. Hold {self.key_name} and talk.", "ok", 4)
        else:
            self.root.after(300, self.ask_api_key)
        log.info("Ready: hotkey %s, models %s + %s", self.settings["hotkey"], self.settings["stt_model"],
                 self.settings["llm_model"])
        self.root.mainloop()

    def quit(self):
        self.listener.stop()
        self.tray.stop()
        self.ui(self.root.quit)

    # -- settings and services

    def reload(self):
        """Settings and modes are re-read before each dictation, so edits apply right away
        (hotkeys and the microphone apply after a restart)."""
        try:
            self.settings = load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
            self.modes = load_json(MODES_PATH, DEFAULT_MODES)
        except (OSError, ValueError) as e:
            log.warning("Kept the previous settings: %s", e)
            self.ui(self.overlay.show, f"A settings file has an error, so the old settings are used: {e}", "warn", 6)

    def mode_config(self, key):
        return self.modes["modes"].get(key) or DEFAULT_MODES["modes"].get(key) or DEFAULT_MODES["modes"]["clean"]

    def _groq_api_key(self):
        return self.settings["groq_api_key"] or os.environ.get("GROQ_API_KEY", "")

    def groq_client(self):
        key = self._groq_api_key()
        if not key:
            return None
        if self._groq is None or self._groq_key != key:
            self._groq, self._groq_key = groq.Groq(api_key=key, max_retries=0), key
        return self._groq

    # -- UI thread helpers

    def ui(self, fn, *args):
        """Run fn on the Tk thread (Tk isn't thread-safe)."""
        self.calls.put((fn, args))

    def _run_calls(self):
        while True:
            try:
                fn, args = self.calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                log.exception("UI update failed")
        self.root.after(40, self._run_calls)

    def toast(self, text, color="ok", seconds=3):
        self.ui(self.overlay.show, text, color, seconds)

    def _make_tray(self):
        redo = [pystray.MenuItem(cfg["label"], self._redo_action(key))
                for key, cfg in self.modes["modes"].items() if key != "edit"]
        languages = [pystray.MenuItem(label, self._language_action(key), radio=True,
                                      checked=self._language_checked(key))
                     for key, (label, _) in LANGUAGES.items()]
        menu = pystray.Menu(
            pystray.MenuItem(f"Hold {self.key_name} to talk, double-tap for hands-free", None, enabled=False),
            pystray.MenuItem(lambda item: self.usage.meter(self.settings), None, enabled=False),
            pystray.MenuItem("Copy last result", lambda: self.jobs.put(("copy_last", None))),
            pystray.MenuItem("Redo last as", pystray.Menu(*redo)),
            pystray.MenuItem("Write in", pystray.Menu(*languages)),
            pystray.MenuItem("Usage stats", self._open_stats),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Set Groq API key…", lambda: self.ui(self.ask_api_key)),
            pystray.MenuItem("Open settings", lambda: open_in_notepad(SETTINGS_PATH)),
            pystray.MenuItem("Open modes", lambda: open_in_notepad(MODES_PATH)),
            pystray.MenuItem("Open history", lambda: open_in_notepad(HISTORY_PATH)),
            pystray.MenuItem("Start with Windows", toggle_startup, checked=lambda item: os.path.exists(STARTUP_LINK)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )
        return pystray.Icon(APP_NAME, tray_image(), f"{APP_NAME}: hold {self.key_name} to talk", menu)

    def _redo_action(self, key):
        # A plain closure: pystray passes the icon to callables that take an argument.
        return lambda: self.jobs.put(("redo", key))

    def _language_action(self, key):
        def choose():
            self.settings["output_language"] = key
            save_json(SETTINGS_PATH, self.settings)
            self.toast(f"Ramble now writes in: {LANGUAGES[key][0]}", "ok", 2.5)
        return choose

    def _language_checked(self, key):
        return lambda item: self.settings["output_language"] == key

    def _open_stats(self):
        with open(USAGE_REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(self.usage.report(self.settings))
        open_in_notepad(USAGE_REPORT_PATH)

    def ask_api_key(self):
        win = tk.Toplevel(self.root)
        win.title("Ramble: Groq API key")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(
            win, justify="left", font=("Segoe UI", 10),
            text="Paste your Groq API key (free).\nCreate one at console.groq.com > API Keys.",
        ).pack(anchor="w", padx=16, pady=(14, 8))
        entry = tk.Entry(win, width=56, show="•", font=("Segoe UI", 10))
        entry.pack(padx=16)

        def save(_event=None):
            key = entry.get().strip()
            if not key:
                return
            self.settings["groq_api_key"] = key
            save_json(SETTINGS_PATH, self.settings)
            win.destroy()
            self.overlay.show(f"Key saved. Hold {self.key_name} and talk.", "ok", 4)

        tk.Button(win, text="Save", width=12, command=save).pack(pady=12)
        win.bind("<Return>", save)
        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
        y = (win.winfo_screenheight() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        win.focus_force()
        entry.focus_set()

    # -- keyboard hook (runs on pynput's thread; must return fast)

    def _filter(self, msg, data):
        if data.flags & LLKHF_INJECTED:
            return True
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if not down and data.vkCode in self.swallowed:
            self.swallowed.discard(data.vkCode)
            self.listener.suppress_event()
        # While recording, Esc cancels and is kept away from the app (it could close your email).
        if down and data.vkCode == VK_ESCAPE and self.state in ("holding", "handsfree"):
            self.events.put(("esc", time.monotonic()))
            self.swallowed.add(VK_ESCAPE)
            self.listener.suppress_event()
        if down and data.vkCode == self.paste_vk and modifiers_held() == self.paste_mods:
            self.events.put(("paste_last", time.monotonic()))
            self.swallowed.add(data.vkCode)
            self.listener.suppress_event()
        return True

    def _on_press(self, key, injected):
        now = time.monotonic()
        if key == self.hotkey:
            if not self.hotkey_down:  # ignore auto-repeat while held
                self.hotkey_down = True
                self.events.put(("down", now))
        elif injected:
            return  # Ramble's own Ctrl+C, Ctrl+V and typing
        elif key in self.mode_keys and self.state != "idle":
            self.events.put(("mode", now))
        elif self.hotkey_down or self.state != "idle":
            self.events.put(("other", now))

    def _on_release(self, key, injected):
        if key == self.hotkey and self.hotkey_down:
            self.hotkey_down = False
            self.events.put(("up", time.monotonic()))

    # -- recording state machine (control thread)

    def _control_loop(self):
        while True:
            timeout = None if self.deadline is None else max(0.0, self.deadline - time.monotonic())
            try:
                kind, when = self.events.get(timeout=timeout)
            except queue.Empty:
                kind, when = "timer", time.monotonic()
            try:
                self._handle(kind, when)
            except Exception as e:
                log.exception("Recording failed")
                self.recorder.stop()
                self._reset()
                self.toast(f"Couldn't record: {e}", "warn", 6)

    def _handle(self, kind, when):
        state = self.state
        if kind == "paste_last":
            if state == "idle":
                self.jobs.put(("paste_last", None))
        elif kind == "down":
            if state == "idle":
                self._begin(when)
            elif state == "tapped":
                self.state, self.deadline = "handsfree", None
                if not self.confirmed:
                    self._confirm()
            elif state == "handsfree":
                self._finish()
        elif kind == "up" and state == "holding":
            if when - self.started < TAP_SECONDS:
                self.state, self.deadline = "tapped", when + DOUBLE_TAP_SECONDS
            else:
                self._finish()
        elif kind == "timer":
            if state == "holding" and not self.confirmed:
                self._confirm()
            elif state == "tapped":
                self._cancel(quiet=True)  # a single tap, not a double-tap
        elif kind == "mode" and state in ("holding", "handsfree"):
            if not self.confirmed:
                self._confirm()
            self._next_mode()
        elif kind in ("mode", "other", "esc") and state == "tapped":
            self._cancel(quiet=True)
        elif kind == "other" and state == "holding":
            self._cancel(quiet=True)  # it was a shortcut like Ctrl+C, not dictation
        elif kind == "esc" and state in ("holding", "handsfree"):
            self._cancel()

    def _begin(self, when):
        """Start recording and lock in the target: the window, the focused field and its selection."""
        self.recorder.start()
        self.state, self.started, self.confirmed, self.deadline = "holding", when, False, when + TAP_SECONDS
        hwnd = user32.GetForegroundWindow()
        process, title = window_info(hwnd)
        self.target = Target(hwnd, process, title, self.uia.focused_field())
        self.mode = self.auto_mode = detect_mode(process, title, self.modes)

    def _confirm(self):
        """The press is a real dictation (held, double-tapped or mode-switched): show it."""
        self.confirmed, self.deadline = True, None
        found = self.target.field
        # Apps that don't show their selection: read it by copying, then put the clipboard back.
        if found.kind in ("text", "unknown") and not found.knows_selection and self.target.process not in NO_COPY_APPS:
            with self.clipwatch.ours():
                saved = save_clipboard()
                changed, text = copy_selection(self.kb)
                if changed:
                    restore_clipboard(saved)
            found.selection = text if text.strip() else ""
        if self.settings["sounds"]:
            play_sound("start")
        self.ui(self.overlay.show_live, self._listening_status, "listen")

    def _listening_status(self):
        secs = int(time.monotonic() - self.started)
        action = "Tap" if self.state == "handsfree" else "Release"
        hint = f"{action} {self.key_name} to finish  ·  {self.mode_key_name}: change mode  ·  Esc: cancel"
        return f"{self.mode_config(self.mode)['label']}   {secs // 60}:{secs % 60:02d}", hint, self.recorder.levels

    def _next_mode(self):
        """Cycle the mode. Edit comes first when text is selected."""
        order = [self.auto_mode] + (["edit"] if self.target.field.selection else [])
        order += [key for key in self.modes["modes"] if key not in order and key != "edit"]
        position = order.index(self.mode) if self.mode in order else -1
        self.mode = order[(position + 1) % len(order)]

    def _finish(self):
        audio = self.recorder.stop()
        job = Job(audio, self.target, self.mode, self.auto_mode, self.started)
        self._reset()
        if self.settings["sounds"]:
            play_sound("stop")
        self.jobs.put(("dictation", job))
        self.toast("Transcribing…", "work", None)

    def _cancel(self, quiet=False):
        was_visible = self.confirmed
        self.recorder.stop()
        self._reset()
        if not quiet:
            self.toast("Cancelled", "info", 1.2)
        elif was_visible:
            self.ui(self.overlay.hide)

    def _reset(self):
        self.state, self.deadline, self.confirmed = "idle", None, False

    # -- worker thread: speech to text, clean-up, inserting

    def _work_loop(self):
        while True:
            kind, payload = self.jobs.get()
            try:
                if kind == "dictation":
                    self._dictate(payload)
                elif kind == "paste_last":
                    self._paste_last()
                elif kind == "copy_last":
                    self._copy_last()
                elif kind == "redo":
                    self._redo(payload)
            except Exception as e:
                log.exception("%s failed", kind)
                self.toast(f"Something went wrong: {e}", "warn", 6)

    def _dictate(self, job):
        self.reload()
        seconds = len(job.audio) / SAMPLE_RATE
        if seconds < MIN_SECONDS:
            self.ui(self.overlay.hide)
            return
        if not has_speech(job.audio):
            self.toast("Didn't hear any speech. Is the mic muted?", "warn", 4)
            return
        if job.target.field.kind == "password":
            self.toast("Ramble doesn't type into password fields.", "warn", 4)
            return
        client = self.groq_client()
        t0 = time.monotonic()
        try:
            raw, engine = self.stt.transcribe(job.audio, client, self.settings)
        except OfflineUnavailable as e:
            self.toast(str(e), "warn", 8)
            return
        t1 = time.monotonic()
        if not raw:
            self.toast("Didn't catch that. Try again a little louder.", "warn", 3)
            return

        spoken, transcript = spoken_mode(raw)
        mode_key = spoken or job.mode
        mode = self.mode_config(mode_key)
        selection = job.target.field.selection
        clipboard = ""
        if "clipboard" in mode.get("context", []) and self.clipwatch.last_copy >= job.started - COPIED_WITHIN:
            clipboard = (get_clipboard_text() or "")[:4000]
        transcript, snippets = add_placeholders(transcript, self.settings["snippets"])
        text, prompt, problem, model = direct_output(transcript, snippets), None, "", None
        if text is None:
            if mode_key == "clean" and len(transcript.split()) < 3:
                text = light_clean(transcript)  # too short to need AI
            elif client is None:
                problem = "no Groq key"
            else:
                self.toast(f"Writing {mode['label'].lower()}…", "work", None)
                system, user = build_prompt(mode_key, mode, job.target, transcript, selection, clipboard,
                                            self.settings["dictionary"], self.settings["output_language"])
                prompt = {"system": system, "user": user}
                try:
                    text, model = rewrite(client, self.settings, system, user, mode.get("required", ""), self.usage)
                    if mode_key == "prompt":
                        text = tidy_prompt(text)
                except Exception as e:
                    log.warning("Clean-up failed: %s", e)
                    problem = describe_error(e)
            if text is None:
                if mode_key == "edit":  # pasting the spoken instruction over the selection would lose it
                    self.toast(f"Couldn't edit: {problem}. Nothing was changed.", "warn", 5)
                    return
                text = light_clean(transcript)
            text = fill_placeholders(text, snippets)
        if mode_key != "edit" and not selection:
            text = fit_at_cursor(text, job.target.field.before)
        t2 = time.monotonic()

        result = self.insert(text, job.target)
        log.info("%s via %s: %.1fs of speech, speech-to-text %.2fs, clean-up %.2fs, insert %s %.2fs",
                 mode_key, engine, seconds, t1 - t0, t2 - t1, result, time.monotonic() - t2)
        add_history({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "app": job.target.process, "title": job.target.title,
            "mode": mode_key, "raw": raw, "transcript": transcript, "snippets": snippets, "selection": selection,
            "field_before": job.target.field.before, "field_after": job.target.field.after, "clipboard": clipboard,
            "prompt": prompt, "output": text, "result": result, "speech_to_text": engine, "model": model,
        })
        self.usage.add_dictation(mode_key, seconds, len(text.split()), model, self.settings)
        if self.tray is not None:
            self.tray.update_menu()  # refresh the limit meter
        if self.usage.should_warn(self.settings):
            self.toast(f"80% of today's Groq limit is used. Ramble switches to {self.settings['fallback_llm_model']} "
                       "when it runs out.", "warn", 6)
        else:
            self._report(result, problem)

    def _report(self, result, problem=""):
        if result == "copied":
            self.toast("Copied - press Ctrl+V", "ok", 4)
        elif result == "unsure":
            self.toast("Couldn't confirm the paste. The text is on your clipboard.", "warn", 5)
        elif result == "interrupted":
            self.toast("Stopped typing because another window came up. The text is on your clipboard.", "warn", 5)
        elif problem:
            self.toast(f"Inserted plain text ({problem})", "warn", 4)
        else:
            self.toast("Done", "ok", 1.2)

    def insert(self, text, target):
        """Paste into the locked-in field. Returns "pasted", "typed", "copied", "unsure" or "interrupted"."""
        found = target.field
        if found.kind == "none":
            self._copy(text)
            return "copied"
        if user32.GetForegroundWindow() != target.hwnd and not focus_window(target.hwnd):
            self._copy(text)
            return "copied"
        if found.element is not None:
            self.uia.ensure_focus(found.element)
        if user32.GetForegroundWindow() != target.hwnd:  # the user switched away at the last moment
            self._copy(text)
            return "copied"
        with self.clipwatch.ours():
            saved = save_clipboard()
            before = self.uia.snapshot(found.element) if found.readable else None
            set_clipboard_text(text)
            press_ctrl(self.kb, VK_V)
            landed = self._wait_for_change(found.element, before) if before is not None else None
            if landed is None:
                time.sleep(0.4)  # can't watch this field, so give the app a moment to read the clipboard
            if landed is not False or found.editable:
                restore_clipboard(saved)
        if landed is False:
            if found.editable:  # the field ignored Ctrl+V (some block pasting): type it instead
                if type_text(self.kb, text, target.hwnd):
                    return "typed"
                self._copy(text)
                return "interrupted"
            return "unsure"  # the text stays on the clipboard
        return "pasted"

    def _copy(self, text):
        with self.clipwatch.ours():
            set_clipboard_text(text)

    def _wait_for_change(self, element, before, seconds=1.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
            now = self.uia.snapshot(element)
            if now is None:
                return None
            if now != before:
                return True
        return False

    def _current_target(self):
        hwnd = user32.GetForegroundWindow()
        process, title = window_info(hwnd)
        return Target(hwnd, process, title, self.uia.focused_field())

    def _paste_last(self):
        entry = last_history()
        if not entry:
            self.toast("Nothing to paste yet", "info", 2)
            return
        self._report(self.insert(entry["output"], self._current_target()))

    def _copy_last(self):
        entry = last_history()
        if entry:
            self._copy(entry["output"])
            self.toast("Last result copied - press Ctrl+V", "ok", 3)

    def _redo(self, mode_key):
        """Rewrite the last dictation in another mode and put it on the clipboard."""
        self.reload()
        entry, client = last_history(), self.groq_client()
        if not entry or client is None:
            self.toast("Nothing to redo yet" if not entry else "Redo needs a Groq API key", "info", 3)
            return
        mode = self.mode_config(mode_key)
        target = Target(0, entry["app"], entry["title"],
                        Field(kind="text", before=entry["field_before"], after=entry["field_after"]))
        self.toast(f"Rewriting as {mode['label'].lower()}…", "work", None)
        system, user = build_prompt(mode_key, mode, target, entry["transcript"], entry["selection"],
                                    entry["clipboard"], self.settings["dictionary"], self.settings["output_language"])
        try:
            text, _ = rewrite(client, self.settings, system, user, mode.get("required", ""), self.usage)
            text = fill_placeholders(tidy_prompt(text) if mode_key == "prompt" else text, entry["snippets"])
        except groq.APIError as e:
            self.toast(f"Couldn't redo: {describe_error(e)}", "warn", 4)
            return
        self._copy(text)
        add_history(dict(entry, mode=mode_key, prompt={"system": system, "user": user}, output=text, result="copied",
                         time=time.strftime("%Y-%m-%d %H:%M:%S")))
        self.toast(f"Rewritten as {mode['label'].lower()}: press Ctrl+V to paste", "ok", 4)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    if sys.stdout is None:  # pythonw has no console, but some libraries still print
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s"
    )
    if already_running():
        message_box("Ramble is already running. Look for the mic icon in the tray, next to the clock.")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # sharp text on high-DPI screens
    except OSError:
        pass
    App().run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Ramble couldn't start")
        message_box(f"Ramble couldn't start:\n\n{e}\n\nDetails: {LOG_PATH}", error=True)
