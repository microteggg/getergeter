"""SMC v2 compatibility build hook. Lazy history patch is intentionally disabled because chart schema changed."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"docs"/"app.js"
def main():
    if APP.exists():
        s=APP.read_text(encoding="utf-8")
        if "v10-lazy" not in s:
            s=s.replace('const APP_VERSION = "v9-light";', 'const APP_VERSION = "v10-lazy";')
            s += "\n// v10-lazy SMC v2 compatibility\n"
            APP.write_text(s,encoding="utf-8")
    print("SMC v2 build hook diterapkan")
if __name__=="__main__": main()
