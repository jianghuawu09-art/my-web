import tkinter as tk
from tkinter import ttk
import pyautogui
import threading
import time

class MouseTracker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("鼠标位置追踪器")
        self.root.geometry("300x150")
        self.root.attributes('-topmost', True)  # 置顶显示
        
        # 让窗口可以拖动
        self.root.overrideredirect(True)  # 去掉标题栏
        self.root.bind('<Button-1>', self.start_move)
        self.root.bind('<B1-Motion>', self.do_move)
        
        # 设置背景色和透明度
        self.root.configure(bg='#2c2c2c')
        self.root.attributes('-alpha', 0.9)
        
        # 创建显示标签
        self.label_x = tk.Label(self.root, text="X: 0", font=("Arial", 16), 
                                fg='#00ff00', bg='#2c2c2c')
        self.label_x.pack(pady=5)
        
        self.label_y = tk.Label(self.root, text="Y: 0", font=("Arial", 16), 
                                fg='#00ff00', bg='#2c2c2c')
        self.label_y.pack(pady=5)
        
        self.label_xy = tk.Label(self.root, text="(0, 0)", font=("Arial", 12), 
                                 fg='#ffffff', bg='#2c2c2c')
        self.label_xy.pack(pady=5)
        
        # 添加退出按钮
        self.exit_btn = tk.Button(self.root, text="退出 (ESC)", 
                                  command=self.root.quit, 
                                  bg='#ff4444', fg='white',
                                  font=("Arial", 10))
        self.exit_btn.pack(pady=5)
        
        # 绑定ESC键退出
        self.root.bind('<Escape>', lambda e: self.root.quit())
        
        # 开始更新鼠标位置
        self.running = True
        self.update_mouse_position()
        
    def start_move(self, event):
        self.x = event.x
        self.y = event.y
        
    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")
        
    def update_mouse_position(self):
        if self.running:
            try:
                x, y = pyautogui.position()
                self.label_x.config(text=f"X: {x}")
                self.label_y.config(text=f"Y: {y}")
                self.label_xy.config(text=f"({x}, {y})")
            except:
                pass
            self.root.after(50, self.update_mouse_position)  # 每50ms更新一次
        
    def run(self):
        self.root.mainloop()
        self.running = False

if __name__ == "__main__":
    # 检查是否安装了pyautogui
    try:
        import pyautogui
    except ImportError:
        print("正在安装 pyautogui...")
        import subprocess
        subprocess.check_call(['pip', 'install', 'pyautogui'])
        import pyautogui
    
    tracker = MouseTracker()
    tracker.run()