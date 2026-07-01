Param(
    [ValidateSet("build", "push")]
    [string]$Mode = "build",
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"

function Read-DotEnv {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "未找到配置文件 $Path，请先复制 .env.example 为 .env 并填写镜像配置。"
    }

    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }

        $parts = $line.Split("=", 2)
        if ($parts.Count -ne 2) { return }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name) { Set-Item -Path "Env:$name" -Value $value }
    }
}

function Require-Env {
    param([string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "缺少必填配置：$Name"
    }
    return $value
}

Read-DotEnv -Path $EnvFile

$imagePushAddress = (Require-Env "IMAGE_PUSH_ADDRESS").TrimEnd("/")
$imageName = (Require-Env "IMAGE_NAME").Trim("/")
$imageTag = Require-Env "IMAGE_TAG"
$image = "${imagePushAddress}/${imageName}:${imageTag}"

Write-Host "构建前端镜像：$image"
docker build -t $image .

if ($Mode -eq "push") {
    $registryAddress = Require-Env "REGISTRY_ADDRESS"
    $registryUsername = Require-Env "REGISTRY_USERNAME"
    $registryPassword = Require-Env "REGISTRY_PASSWORD"
    $loggedIn = $false

    try {
        Write-Host "登录镜像仓库：$registryAddress"
        $registryPassword | docker login $registryAddress --username $registryUsername --password-stdin
        $loggedIn = $true

        Write-Host "推送前端镜像：$image"
        docker push $image

        Write-Host "前端镜像已推送：$image"
    } finally {
        if ($loggedIn) {
            Write-Host "退出镜像仓库登录：$registryAddress"
            docker logout $registryAddress | Out-Null
        }
    }
} else {
    Write-Host "前端镜像已构建：$image"
}


