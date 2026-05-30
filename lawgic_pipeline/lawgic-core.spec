# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — freeze the Lawgic FEK pipeline into a self-contained core.

Produces a ONEDIR bundle (dist/lawgic-core/) whose entry binary is
`lawgic-core[.exe]`. Onedir (not onefile) is deliberate: the Electron shell
spawns the core once per command (status / review / ingest / retry), and a
onefile build would re-extract itself to a temp dir on every spawn — slow and a
frequent trigger for Windows AV heuristics. Onedir starts fast and ships as a
plain folder the installer drops next to the app.

Build (on the target OS — Windows for the .exe):
    pip install -r requirements.txt pyinstaller
    pyinstaller lawgic-core.spec --noconfirm

The Electron shell picks up dist/lawgic-core/ via electron-builder extraResources.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

# Third-party packages that carry data files or rely on dynamic submodule
# imports PyInstaller's static analysis can miss. collect_all pulls in their
# binaries, datas (e.g. pdfminer's CMap tables) and hidden submodules.
datas, binaries, hiddenimports = [], [], []
for pkg in ("weaviate", "voyageai", "pdfplumber", "pdfminer", "anthropic",
            "openai", "azure.ai.documentintelligence", "grpc"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Some dependencies look up their own version at runtime via
# importlib.metadata.version(...) — e.g. weaviate.proto reads "protobuf" — which
# needs the package's .dist-info METADATA bundled. collect_all ships code/data
# but NOT this metadata, so copy it explicitly (recursive = include the whole
# dependency tree, covering protobuf/grpcio and anything else weaviate queries).
for dist in ("weaviate-client", "protobuf", "grpcio"):
    datas += copy_metadata(dist, recursive=True)

# Local modules reached only through function-level / late imports (the sidecar
# detector and the per-stage pipeline package). Listed explicitly so they are
# always frozen even if a future refactor hides the import from static analysis.
hiddenimports += [
    "sidecar.pdf_extract",
    *collect_submodules("pipeline"),
    # provider SDKs are imported lazily inside llm.py by provider name
    "anthropic", "openai",
]

a = Analysis(
    ["cli.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # keep the bundle lean: nothing here needs notebooks / GUI / test frameworks
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6", "IPython",
              "pytest", "numpy.testing"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lawgic-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX off: it trips Windows AV more than it saves
    console=True,              # the shell reads stdout/stderr line-by-line
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="lawgic-core",
)
