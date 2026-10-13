# Building the Breezify release package

Breezify is a single Python script (`breezify.py`). The release zip adds a private Python runtime so
users don't need to install anything. Every binary in the zip comes from an official upstream
release, except `meshopt.dll`, which is compiled from the official meshoptimizer source as
described below.

## Contents of the release zip

| Path | What it is | Origin |
|---|---|---|
| `breezify.py` | the tool (all of the logic, plain Python source) | this repository |
| `Breezify.bat`, `Breezify_mgexe.bat` | drag-and-drop launchers; they run `python\python.exe breezify.py` | this repository |
| `python\` | Windows embeddable Python 3.13.16 (64-bit), Authenticode-signed by the Python Software Foundation | python.org (see below) |
| `python\Lib\site-packages\numpy` | numpy 2.5.4 | official PyPI wheel (see below) |
| `meshopt.dll` | meshoptimizer v0.25 (MIT), used only to simplify the distant `_dist` meshes | compiled from source (see below) |

Breezify makes no network connections. The bundled runtime doesn't even contain Python's
networking/SSL modules (they were removed, see step 2).

## 1. Python runtime

```
https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip
SHA-256 97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297  (as published on python.org)
```

Extract it to `python\`. Then:

- Extract `python\python313.zip` (the standard library) into `python\Lib\` and delete the zip,
  so the release contains no nested archive.
- Replace the contents of `python\python313._pth` with these three lines:
  ```
  Lib
  .
  Lib\site-packages
  ```

## 2. Remove unused runtime parts

Breezify only needs the core interpreter, `ctypes` (to call `meshopt.dll`) and numpy, so these files
are deleted from `python\`:

```
pythonw.exe libcrypto-3.dll libssl-3.dll _ssl.pyd _hashlib.pyd sqlite3.dll _sqlite3.pyd _socket.pyd
select.pyd _asyncio.pyd _overlapped.pyd _multiprocessing.pyd _queue.pyd _wmi.pyd winsound.pyd
_uuid.pyd _zoneinfo.pyd _decimal.pyd _elementtree.pyd pyexpat.pyd _bz2.pyd _lzma.pyd
```

## 3. numpy

```
numpy-2.5.4-cp313-cp313-win_amd64.whl   (official wheel from PyPI)
SHA-256 8b4d2fd2d34e5f8c9235ee787de5631a37a28402b15cb80814df973d2be54129
```

A wheel is a zip file. Extract it into `python\Lib\site-packages\`. Then remove numpy's self-tests
and developer files: every `tests` folder, `f2py`, `_pyinstaller`, `typing`, `testing\_private`,
`random\_examples`, `_core\include`, `_core\lib`, all `*.lib`, `*.pyi`, `*.pxd` and `*.h` files,
and the `*_tests.cp313-win_amd64.pyd` test modules.

## 4. meshopt.dll

Source: https://github.com/zeux/meshoptimizer, tag `v0.25`
(commit `6daea4695c48338363b08022d2fb15deaef6ac09`), MIT license (included as
`meshoptimizer_LICENSE.md`).

Built with MinGW-w64 (GCC 13.2), statically linked, so its only dependencies are `KERNEL32.dll` and
`msvcrt.dll`:

```
git clone --depth 1 --branch v0.25 https://github.com/zeux/meshoptimizer.git
cd meshoptimizer
x86_64-w64-mingw32-g++ -O2 -shared -static -static-libgcc -static-libstdc++ \
  -DMESHOPTIMIZER_API='extern "C" __declspec(dllexport)' -o meshopt.dll src/*.cpp
```

An equivalent build with Visual Studio (Developer Command Prompt, x64):

```
cl /O2 /LD /EHsc /DMESHOPTIMIZER_API="extern \"C\" __declspec(dllexport)" src\*.cpp /Fe:meshopt.dll
```

Breezify calls only one function from it, `meshopt_simplifyWithAttributes`, through Python's `ctypes`
(see `_meshopt()` in `breezify.py`). If `meshopt.dll` is missing, Breezify still works; it just
doesn't reduce the triangle count of the `_dist` meshes.

SHA-256 of the shipped `meshopt.dll` (a GCC build isn't byte-for-byte reproducible, so a rebuild
can differ in its bytes, not in function):
`06b405035349c1727e4e65457b337f77e7e54544e59898b0c2c64cfd99850c57`

## 5. Assemble

```
Vurt's Breezify\
  Breezify.bat
  Breezify_mgexe.bat
  breezify.py
  README.txt
  meshopt.dll
  meshoptimizer_LICENSE.md
  python\            (steps 1-3)
```

Check it runs: `python\python.exe breezify.py some_tree.nif`

## Running without the bundled runtime

With your own Python 3.8+ and numpy (`pip install numpy`): `python breezify.py <files or folders>`.
On Linux, build `libmeshopt.so` from the same source (`g++ -O2 -shared -fPIC ... -o libmeshopt.so
src/*.cpp`) and put it next to the script.
