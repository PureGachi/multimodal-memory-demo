# Run in the PowerShell that will launch the API or inspection scripts.
# No system-wide settings or shell profile changes.
$ocrProjectRoot = Split-Path -Parent $PSScriptRoot
$ocrExecutable = Join-Path $ocrProjectRoot '.conda\Library\bin\tesseract.exe'
$ocrDataDirectory = Join-Path $ocrProjectRoot '.conda\share\tessdata'
if (-not (Test-Path -LiteralPath $ocrExecutable -PathType Leaf)) {
    throw 'Tesseract not found in the project Conda environment.'
}
foreach ($ocrLanguage in @('chi_sim', 'eng')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ocrDataDirectory ($ocrLanguage + '.traineddata')) -PathType Leaf)) {
        throw ('Missing OCR language: ' + $ocrLanguage)
    }
}
$env:MEMORY_OCR = 'tesseract'
$env:MEMORY_VISION = 'none'
$env:TESSERACT_EXECUTABLE = $ocrExecutable
$env:TESSDATA_PREFIX = $ocrDataDirectory
$env:OCR_LANGUAGES = 'chi_sim'
# eng is also installed. Compare eng / chi_sim+eng on your own images before switching.
Write-Host 'Local OCR enabled using chi_sim; eng is also installed. Remote vision disabled.'
Write-Host 'MEMORY_EMBEDDINGS is unchanged. Set it to none for OCR + BM25 without Ollama.'
