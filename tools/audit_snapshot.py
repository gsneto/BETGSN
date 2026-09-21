"""Snapshot de codigo (sem credenciais/datasets) e inventario reproduzivel."""
import ast
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "engineering"
OUT.mkdir(parents=True, exist_ok=True)
files = [p for folder in (ROOT / "betgsn", ROOT / "tests", ROOT / "web" / "src")
         for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
files += [ROOT / n for n in ("betgsn.py", "README.md", "requirements.txt", "requirements-api.txt", "web/package.json")]
manifest = {}
with ZipFile(OUT / "original-source.zip", "x", ZIP_DEFLATED) as archive:
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        archive.write(p, rel)
        info = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        if p.suffix == ".py":
            tree = ast.parse(p.read_text(encoding="utf-8-sig"))
            info["symbols"] = [{"name": n.name, "line": n.lineno}
                               for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        manifest[rel] = info
(OUT / "original-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"Snapshot: {len(files)} arquivos; {OUT}")
