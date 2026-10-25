param(
    [Parameter(Mandatory=$true)][string]$EmbeddingBaseUrl,
    [switch]$WithoutVision
)

# Changes this PowerShell session only. Does not send any paid requests.
# Keys are supplied separately via environment variables, never printed here.
$env:MEMORY_PROFILE = 'competition'
$env:MEMORY_DATA_DIR = 'data/official/evaluation/runtime'
$env:MEMORY_EMBEDDINGS = 'compatible'
$env:EMBEDDING_BASE_URL = $EmbeddingBaseUrl
$env:EMBEDDING_MODEL = 'text-embedding-v4'
$env:EMBEDDING_DIMENSIONS = '1024'
$env:EMBEDDING_REVISION = '1'
$env:MEMORY_VISION = if ($WithoutVision) { 'none' } else { 'compatible' }
$env:VISION_BASE_URL = 'https://api.openai.com/v1'
$env:VISION_MODEL = 'gpt-4o-mini'
$env:VISION_REVISION = '1'
$env:MEMORY_OCR = 'none'
Write-Host 'Competition model settings selected; no network calls or data migration performed.'
Write-Host 'Formal data uses data/official/evaluation/runtime; register the empty parent with retention.py before first use.'
Write-Host 'Set EMBEDDING_API_KEY, VISION_API_KEY (unless -WithoutVision), and MEMORY_API_KEY privately.'
Write-Host 'Use a new service process. Existing lab/API processes keep their old environment.'
