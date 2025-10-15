import sys
import numpy as np
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QLineEdit,
    QCheckBox,
)
from PySide6.QtCore import Qt, QTimer
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class SinusWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Exercice sinus animé ✨")

        # ---------- 初始参数 ----------
        self.frequency = 1.0
        self.amplitude = 1.0
        self.phase = 0.0
        self.phase_increment = 0.1  # 每帧相位变化量

        # ---------- Matplotlib 图像 ----------
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot()
        self.x = np.linspace(0, 10, 400)
        (self.line,) = self.ax.plot(
            self.x, self.amplitude * np.sin(self.frequency * self.x + self.phase)
        )
        self.ax.set_title("sinus animé")
        self.ax.grid(True)

        # ---------- 界面布局 ----------
        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas)

        # 三个滑块控制 frequency, amplitude, phase
        layout.addLayout(
            self.make_control("Fréquence", 0.1, 10, self.frequency, "frequency")
        )
        layout.addLayout(
            self.make_control("Amplitude", 0.1, 5, self.amplitude, "amplitude")
        )
        layout.addLayout(self.make_control("Phase", 0.0, 6.28, self.phase, "phase"))

        # 动画控制复选框
        self.animate_checkbox = QCheckBox("Animer la phase")
        self.animate_checkbox.stateChanged.connect(self.toggle_animation)
        layout.addWidget(self.animate_checkbox)

        # ---------- 定时器 ----------
        self.timer = QTimer()
        self.timer.timeout.connect(self.animate_phase)

    # ---------- 创建滑块+文本输入 ----------
    def make_control(self, label_text, min_val, max_val, default, attr):
        hbox = QHBoxLayout()
        label = QLabel(label_text)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(100)
        slider.setValue(int(100 * (default - min_val) / (max_val - min_val)))

        line_edit = QLineEdit(f"{default:.2f}")

        setattr(self, f"{attr}_slider", slider)
        setattr(self, f"{attr}_edit", line_edit)
        setattr(self, f"{attr}_min", min_val)
        setattr(self, f"{attr}_max", max_val)

        slider.valueChanged.connect(lambda val, a=attr: self.slider_changed(a, val))
        line_edit.editingFinished.connect(lambda a=attr: self.text_edited(a))

        hbox.addWidget(label)
        hbox.addWidget(slider)
        hbox.addWidget(line_edit)
        return hbox

    # ---------- 滑块回调 ----------
    def slider_changed(self, attr, value):
        min_val = getattr(self, f"{attr}_min")
        max_val = getattr(self, f"{attr}_max")
        real_value = min_val + (max_val - min_val) * value / 100

        setattr(self, attr, real_value)
        line_edit = getattr(self, f"{attr}_edit")
        line_edit.setText(f"{real_value:.2f}")
        self.update_plot()

    # ---------- 文本框回调 ----------
    def text_edited(self, attr):
        line_edit = getattr(self, f"{attr}_edit")
        text = line_edit.text()
        try:
            val = float(text)
        except ValueError:
            line_edit.setText(f"{getattr(self, attr):.2f}")
            return

        min_val = getattr(self, f"{attr}_min")
        max_val = getattr(self, f"{attr}_max")

        if min_val <= val <= max_val:
            setattr(self, attr, val)
            slider = getattr(self, f"{attr}_slider")
            slider_val = int(100 * (val - min_val) / (max_val - min_val))
            slider.setValue(slider_val)
        else:
            line_edit.setText(f"{getattr(self, attr):.2f}")

    # ---------- 更新图像 ----------
    def update_plot(self):
        y = self.amplitude * np.sin(self.frequency * self.x + self.phase)
        self.line.set_ydata(y)
        self.canvas.draw_idle()

    # ---------- 动画：自动修改相位 ----------
    def animate_phase(self):
        new_phase = self.phase + self.phase_increment
        # 相位循环到 0~2π 之间
        if new_phase > 2 * np.pi:
            new_phase -= 2 * np.pi
        self.phase = new_phase

        # 同步滑块显示
        slider = self.phase_slider
        min_val = self.phase_min
        max_val = self.phase_max
        slider_val = int(100 * (self.phase - min_val) / (max_val - min_val))
        slider.setValue(slider_val)

    # ---------- 动画开关 ----------
    def toggle_animation(self, state):
        if state:
            self.timer.start(20)  # 每 20 ms 更新一次
        else:
            self.timer.stop()


# ---------- 主程序 ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = SinusWidget()
    win.resize(700, 500)
    win.show()
    sys.exit(app.exec())
