@echo off
REM Build the GitHub Pages output for the Hanwha Life disclosure index.
REM ASCII only, CRLF. See docs in scripts\build_site.py for the design constraints.
REM   build.bat            -> fetch + jsonld + site
REM   build.bat site       -> site only (reuse data\products.json)
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python launcher "py" not found on PATH.
  exit /b 1
)

py -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.12 or newer is required.
  py --version
  exit /b 1
)

if /i "%~1"=="site" goto SITE

echo [1/3] fetch product disclosure data
py scripts\fetch_products.py
if errorlevel 1 exit /b 1

echo [2/3] build standalone JSON-LD
py scripts\build_jsonld.py
if errorlevel 1 exit /b 1

:SITE
echo [3/3] build static site
py scripts\build_site.py --clean --out _site
if errorlevel 1 exit /b 1

echo.
echo Done. Output is in _site\. Deployment is done by GitHub Actions on push
echo (.github\workflows\refresh.yml); _site\ is not committed.
exit /b 0
