"""Run Windows file dialogs in their own process, with their own Tk main thread."""
import json
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
if len(sys.argv) > 1 and sys.argv[1] == 'directory':
    path = filedialog.askdirectory(title='Папка для готовых роликов', parent=root)
else:
    path = filedialog.askopenfilename(title='Выберите видео', parent=root,
        filetypes=[('Видео', '*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.ts'), ('Все файлы', '*.*')])
root.destroy()
print(json.dumps({'path': path}, ensure_ascii=True))
