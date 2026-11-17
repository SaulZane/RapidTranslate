# Build with: python -m PyInstaller --noconfirm IndexTranslate-Books.spec
a = Analysis(['gui.py'], pathex=[], binaries=[],
             datas=[('bd_runner.py', '.'), ('azw3_runner.py', '.'), ('font_preferences.py', '.'),
                    ('babeldoc_fonts.py', '.'), ('ocr_runner.py', '.')],
             hiddenimports=['win32timezone'], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='IndexTranslate-Books',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False)
