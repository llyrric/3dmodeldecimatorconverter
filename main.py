import sys
import os
import subprocess
import threading
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QLineEdit, QPushButton, QFileDialog, QComboBox, 
                               QSpinBox, QCheckBox, QTextEdit, QProgressBar, QGroupBox, 
                               QFormLayout)
from PySide6.QtCore import Qt, Signal, QObject, QProcess

class ProcessorWorker(QObject):
    finished = Signal(int, str)
    log_received = Signal(str)
    
    def __init__(self, blender_path, script_path, args):
        super().__init__()
        self.blender_path = blender_path
        self.script_path = script_path
        self.args = args
        self.process = None

    def run(self):
        full_cmd = [
            self.blender_path,
            "--background",
            "--python", self.script_path,
            "--"
        ] + self.args
        
        self.log_received.emit(f"Running command: {' '.join(full_cmd)}\n")
        
        self.process = QProcess()
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_output)
        self.process.finished.connect(self.handle_finished)
        self.process.start(self.blender_path, ["--background", "--python", self.script_path, "--"] + self.args)

    def handle_output(self):
        data = self.process.readAllStandardOutput().data().decode()
        self.log_received.emit(data)

    def handle_finished(self, exit_code):
        self.finished.emit(exit_code, "Processing Finished")

class DecimatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3D Model Decimator & Baker")
        self.setMinimumWidth(700)
        self.setMinimumHeight(800)
        
        # Default Blender Path Search
        self.blender_path = self.find_blender()
        
        self.init_ui()
        self.apply_styles()

    def find_blender(self):
        # 1. Check for a local 'blender' folder in the same directory (for portable bundling)
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        local_blender = os.path.join(app_dir, "blender", "blender.exe")
        if os.path.exists(local_blender):
            return local_blender
            
        # 2. Common Windows paths
        paths = [
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
        ]
        for p in paths:
            if os.path.exists(p):
                return p
        return "blender" # Fallback to PATH

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Title
        title = QLabel("3D Model Decimator & Baker")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # File Selection Group
        file_group = QGroupBox("File Selection")
        file_layout = QFormLayout(file_group)
        
        self.input_edit = QLineEdit()
        input_btn = QPushButton("Browse...")
        input_btn.clicked.connect(self.browse_input)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit)
        input_row.addWidget(input_btn)
        file_layout.addRow("Input Model:", input_row)

        self.output_edit = QLineEdit()
        output_btn = QPushButton("Browse...")
        output_btn.clicked.connect(self.browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(output_btn)
        file_layout.addRow("Output Filename:", output_row)
        
        layout.addWidget(file_group)

        # Settings Group
        settings_group = QGroupBox("Decimation & Baking Settings")
        settings_layout = QFormLayout(settings_group)
        
        self.tri_limit = QSpinBox()
        self.tri_limit.setRange(100, 1000000)
        self.tri_limit.setValue(20000)
        settings_layout.addRow("Target Triangles:", self.tri_limit)
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["obj", "fbx", "glb"])
        self.format_combo.currentIndexChanged.connect(self.update_output_extension)
        settings_layout.addRow("Output Format:", self.format_combo)
        
        self.res_combo = QComboBox()
        self.res_combo.addItems(["1024", "2048", "4096"])
        self.res_combo.setCurrentText("2048")
        settings_layout.addRow("Texture Resolution:", self.res_combo)
        
        self.bake_diffuse = QCheckBox("Diffuse")
        self.bake_diffuse.setChecked(True)
        self.bake_normal = QCheckBox("Normal")
        self.bake_normal.setChecked(True)
        self.bake_rough = QCheckBox("Roughness")
        self.bake_rough.setChecked(True)
        self.bake_metal = QCheckBox("Metallic")
        self.bake_metal.setChecked(True)
        
        bake_row = QHBoxLayout()
        bake_row.addWidget(self.bake_diffuse)
        bake_row.addWidget(self.bake_normal)
        bake_row.addWidget(self.bake_rough)
        bake_row.addWidget(self.bake_metal)
        settings_layout.addRow("Bake Maps:", bake_row)
        
        layout.addWidget(settings_group)
        
        # Blender Path Group
        blender_group = QGroupBox("System")
        blender_layout = QFormLayout(blender_group)
        self.blender_edit = QLineEdit(self.blender_path)
        blender_btn = QPushButton("Find...")
        blender_btn.clicked.connect(self.browse_blender)
        blender_row = QHBoxLayout()
        blender_row.addWidget(self.blender_edit)
        blender_row.addWidget(blender_btn)
        blender_layout.addRow("Blender Path:", blender_row)
        layout.addWidget(blender_group)

        # Process Button
        self.process_btn = QPushButton("Start Decimation & Baking")
        self.process_btn.setObjectName("ProcessButton")
        self.process_btn.clicked.connect(self.start_process)
        layout.addWidget(self.process_btn)

        # Progress & Logs
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.log_text.setPlaceholderText("Logs will appear here...")
        layout.addWidget(self.log_text)

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QLabel {
                color: #e0e0e0;
                font-size: 14px;
            }
            #Title {
                font-size: 24px;
                font-weight: bold;
                margin-bottom: 20px;
                color: #00adb5;
            }
            QGroupBox {
                border: 1px solid #333333;
                border-radius: 8px;
                margin-top: 20px;
                font-weight: bold;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #00adb5;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 5px;
                color: #ffffff;
            }
            QComboBox QAbstractItemView {
                background-color: #2d2d2d;
                color: #ffffff;
                selection-background-color: #00adb5;
            }
            QCheckBox {
                color: #ffffff;
            }
            QPushButton {
                background-color: #393e46;
                border: none;
                border-radius: 4px;
                padding: 8px 15px;
                color: #ffffff;
            }
            QPushButton:hover {
                background-color: #4e545e;
            }
            #ProcessButton {
                background-color: #00adb5;
                font-size: 16px;
                font-weight: bold;
                padding: 12px;
            }
            #ProcessButton:hover {
                background-color: #00cfd8;
            }
            QTextEdit {
                background-color: #121212;
                border: 1px solid #333333;
                border-radius: 4px;
                color: #d0d0d0;
                font-family: 'Consolas', 'Courier New', monospace;
            }
            QProgressBar {
                border: 1px solid #333333;
                border-radius: 4px;
                text-align: center;
                height: 20px;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #00adb5;
            }
        """)

    def browse_input(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select High Poly Model", "", "3D Models (*.obj *.fbx *.glb *.gltf)")
        if file_path:
            self.input_edit.setText(file_path)
            # Auto-fill output if empty or has wrong extension
            current_out = self.output_edit.text()
            if not current_out:
                base, _ = os.path.splitext(file_path)
                ext = "." + self.format_combo.currentText()
                self.output_edit.setText(base + "_low" + ext)
            else:
                self.update_output_extension()

    def browse_output(self):
        ext = self.format_combo.currentText()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Decimated Model", self.output_edit.text(), f"Model (*.{ext})")
        if file_path:
            self.output_edit.setText(file_path)

    def update_output_extension(self):
        current_path = self.output_edit.text()
        if not current_path:
            return
            
        new_ext = "." + self.format_combo.currentText()
        base, old_ext = os.path.splitext(current_path)
        
        if old_ext.lower() != new_ext.lower():
            self.output_edit.setText(base + new_ext)

    def browse_blender(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select blender.exe", "", "Executable (*.exe)")
        if file_path:
            self.blender_edit.setText(file_path)

    def start_process(self):
        input_file = self.input_edit.text()
        output_file = self.output_edit.text()
        blender_path = self.blender_edit.text()
        
        if not input_file or not output_file:
            self.log_text.append("Error: Please select input and output files.")
            return

        # Prepare arguments for processor.py
        args = [
            "--input", input_file,
            "--output", output_file,
            "--triangles", str(self.tri_limit.value()),
            "--format", self.format_combo.currentText(),
            "--resolution", self.res_combo.currentText()
        ]
        if self.bake_diffuse.isChecked():
            args.append("--bake_diffuse")
        if self.bake_normal.isChecked():
            args.append("--bake_normal")
        if self.bake_rough.isChecked():
            args.append("--bake_roughness")
        if self.bake_metal.isChecked():
            args.append("--bake_metallic")

        self.log_text.clear()
        self.log_text.append("Starting Process...\n")
        self.process_btn.setEnabled(False)
        self.progress.setRange(0, 0) # Pulsing progress
        
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processor.py")
        
        # Start worker
        self.worker = ProcessorWorker(blender_path, script_path, args)
        self.worker.log_received.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.run()

    def append_log(self, text):
        self.log_text.insertPlainText(text)
        # Auto-scroll to bottom
        self.log_text.ensureCursorVisible()

    def on_finished(self, exit_code, status):
        self.process_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if exit_code == 0 else 0)
        self.log_text.append(f"\n--- {status} ---")
        if exit_code != 0:
            self.log_text.append(f"Process failed with exit code {exit_code}")
        else:
            self.log_text.append("SUCCESS: Model saved to " + self.output_edit.text())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DecimatorApp()
    window.show()
    sys.exit(app.exec())
