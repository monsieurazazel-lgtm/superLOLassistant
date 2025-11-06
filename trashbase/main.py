import ctypes
import time
import random
import threading
from pynput import keyboard
import pyperclip
import os

# Windows API 定义
SendInput = ctypes.windll.user32.SendInput

# 输入事件结构体
PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]


def press_enter():
    ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)  # Enter down
    ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)  # Enter up


def send_unicode_char(char):
    """发送单个Unicode字符"""
    uni_input = Input(type=1, ii=Input_I())
    uni_input.ii.ki = KeyBdInput(
        0, ord(char), 0x0004, 0, ctypes.pointer(ctypes.c_ulong(0))
    )  # KEYEVENTF_UNICODE = 0x0004
    SendInput(1, ctypes.pointer(uni_input), ctypes.sizeof(uni_input))

    uni_input.ii.ki.dwFlags = 0x0006  # KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    SendInput(1, ctypes.pointer(uni_input), ctypes.sizeof(uni_input))


def send_text_to_game(text):
    """直接在游戏中输入任意语言字符"""
    time.sleep(0.3)
    press_enter()  # 打开聊天框
    time.sleep(0.1)

    for ch in text:
        send_unicode_char(ch)
        time.sleep(0.003)  # 防止太快

    time.sleep(0.1)
    press_enter()  # 发送消息
    print(f"[{time.strftime('%H:%M:%S')}] 已发送：{text}")


# ================= 主监听逻辑 ==================

TAUNTS_FILE = "taunts.txt"
DEBOUNCE_SEC = 0.15


def load_taunts(path=TAUNTS_FILE):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        if lines:
            return lines
    return ["这是默认语句，替换 taunts.txt 以改变内容。"]


taunts = load_taunts()
last_press_time = 0.0
lock = threading.Lock()

TEXT_MAP = {
    "+": "男的来了女的来了男同来了女同来了萝莉来了御姐来了男娘来了双性人来了跨性别来了性别酷儿来了流性人来了武装直升机来了沃尔玛购物袋来了自来水管来了小孩姐来了孙笑川来了嘉然来了多首的怪物来了户晨风来了PDD来了侯国玉来了虎哥来了刀哥来了唐老鸭来了小亮来了.",
    "*": "유럽 ​​대륙을 떠도는 유령, 바로 공산주의라는 유령입니다. 옛 유럽의 모든 세력이 이 유령을 몰아내기 위한 거룩한 투쟁에 힘을 합쳤습니다. 교황과 차르, 메테르니히와 기조, 프랑스 급진파와 독일 경찰 스파이까지 말입니다.",
    "/": "프롤레타리아트가 계급으로, 나아가 정당으로 조직되는 과정은 노동자들 사이의 경쟁으로 인해 끊임없이 약화됩니다. 그러나 이 조직은 끊임없이 재생산되며, 매번 더욱 강하고, 더욱 견고하고, 더욱 강력해집니다. 부르주아 내부의 분열을 이용하여 노동자들의 개인적 이익을 법적으로 인정하도록 강요합니다. 영국의 10시간 노동법안이 그 예입니다.",
}


def on_press(key):
    global last_press_time
    now = time.time()

    with lock:
        if now - last_press_time < DEBOUNCE_SEC:
            return
        last_press_time = now

    try:
        if hasattr(key, "char") and key.char == "-":
            msg = random.choice(taunts)
            pyperclip.copy(msg)
            send_text_to_game(msg)
            return

        elif hasattr(key, "char") and key.char in TEXT_MAP:
            msg = TEXT_MAP[key.char]
            pyperclip.copy(msg)
            send_text_to_game(msg)
            return

    except AttributeError:
        pass

    if key == keyboard.Key.esc:
        print("检测到 Esc，程序退出。")
        return False


def main():
    print("监听启动：按 '-' 或 '+/*//' 触发发送。按 Esc 退出。")
    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    main()
