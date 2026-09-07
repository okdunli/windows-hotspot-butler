<#
    tethering.ps1 -- Windows 10/11 移动热点 (WinRT NetworkOperatorTetheringManager) 桥接脚本
    由 WiFi 热点管理器 (Python) 调用，所有结果以单行 JSON 输出。

    必须使用 Windows PowerShell 5.1 (powershell.exe) 运行，
    PowerShell 7 (pwsh.exe) 默认无法使用 WinRT 类型加速器。

    Action:
      all        查询状态 + 配置 + 客户端 (一次调用拿全部，供轮询使用)
      status     仅状态与配置
      clients    仅客户端列表
      configure  修改 SSID / 密码 / 频段
      start      启动热点
      stop       停止热点
      probe      仅检测 API 是否可用
#>
[CmdletBinding()]
param(
    [string]$Action = "all",
    [string]$SsidB64 = "",
    [string]$PassB64 = "",
    [string]$Band = "keep"      # keep | auto | 2.4 | 5
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# 显式加载 WinRT 运行时支撑程序集，使 [System.WindowsRuntimeSystemExtensions]
# (AsTask 扩展方法) 在本会话中可用；否则 start/stop/configure 的异步等待会失败。
try { Add-Type -AssemblyName "System.Runtime.WindowsRuntime" -ErrorAction Stop } catch {}
try { [void][System.Reflection.Assembly]::LoadWithPartialName("System.Runtime.WindowsRuntime") } catch {}

$out = [ordered]@{ ok = $true; action = $Action; error = ""; errorType = "" }

function Await-Op($WinRtTask, $ResultType) {
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]
    $generic = $asTask.MakeGenericMethod($ResultType)
    $netTask = $generic.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

function Await-Action($WinRtAction) {
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.GetParameters().Count -eq 1 -and
            -not $_.IsGenericMethod -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
        })[0]
    $netTask = $asTask.Invoke($null, @($WinRtAction))
    $netTask.Wait(-1) | Out-Null
}

function Get-Manager {
    [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime] | Out-Null
    [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime] | Out-Null
    [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime] | Out-Null

    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($null -eq $profile) {
        # 没有可共享的上网连接时，退化为使用第一个可用连接档案（此时只能改配置，无法启动共享）
        throw "NO_INTERNET_PROFILE"
    }
    return [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
}

function Band-Enum([string]$b) {
    switch ($b) {
        "2.4" { return [Windows.Networking.NetworkOperators.TetheringWiFiBand]::TwoPointFourGigahertz }
        "5" { return [Windows.Networking.NetworkOperators.TetheringWiFiBand]::FiveGigahertz }
        default { return [Windows.Networking.NetworkOperators.TetheringWiFiBand]::Auto }
    }
}

function Band-Name($enumVal) {
    switch ("$enumVal") {
        "TwoPointFourGigahertz" { return "2.4" }
        "FiveGigahertz" { return "5" }
        "Auto" { return "auto" }
        default { return "auto" }
    }
}

function Read-Status($mgr) {
    $cfg = $mgr.GetCurrentAccessPointConfiguration()
    $out.state = "$($mgr.TetheringOperationalState)"
    $out.ssid = $cfg.Ssid
    $out.passphrase = $cfg.Passphrase
    try { $out.clientCount = [int]$mgr.ClientCount } catch { $out.clientCount = 0 }
    try { $out.maxClientCount = [int]$mgr.MaxClientCount } catch { $out.maxClientCount = 0 }
    try { $out.band = Band-Name $cfg.Band } catch { $out.band = "unsupported"; $out.bandSupported = $false }
    if (-not $out.Contains("bandSupported")) { $out.bandSupported = $true }
    $b24 = $true; $b5 = $false
    try { $b24 = [bool]$cfg.IsBandSupported([Windows.Networking.NetworkOperators.TetheringWiFiBand]::TwoPointFourGigahertz) } catch {}
    try { $b5 = [bool]$cfg.IsBandSupported([Windows.Networking.NetworkOperators.TetheringWiFiBand]::FiveGigahertz) } catch {}
    $out.band24Supported = $b24
    $out.band5Supported = $b5
}

function Read-Clients($mgr) {
    $list = @()
    try {
        foreach ($c in $mgr.GetTetheringClients()) {
            $ips = @()
            foreach ($h in $c.HostNames) { $ips += "$($h.CanonicalName)" }
            $list += , [ordered]@{ mac = "$($c.MacAddress)"; ips = $ips }
        }
        $out.clientsSupported = $true
    }
    catch {
        $out.clientsSupported = $false
    }
    $out.clients = $list
}

try {
    switch ($Action) {
        "probe" {
            $mgr = Get-Manager
            Read-Status $mgr
        }
        "status" {
            $mgr = Get-Manager
            Read-Status $mgr
        }
        "clients" {
            $mgr = Get-Manager
            Read-Clients $mgr
        }
        "all" {
            $mgr = Get-Manager
            Read-Status $mgr
            Read-Clients $mgr
        }
        "configure" {
            $mgr = Get-Manager
            $cfg = $mgr.GetCurrentAccessPointConfiguration()
            if ($SsidB64 -ne "") {
                $cfg.Ssid = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($SsidB64))
            }
            if ($PassB64 -ne "") {
                $cfg.Passphrase = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PassB64))
            }
            if ($Band -ne "keep") {
                try { $cfg.Band = Band-Enum $Band } catch { $out.bandWarning = "$($_.Exception.Message)" }
            }
            Await-Action ($mgr.ConfigureAccessPointAsync($cfg))
            Read-Status $mgr
        }
        "start" {
            $mgr = Get-Manager
            $res = Await-Op ($mgr.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
            $out.result = "$($res.Status)"
            if ("$($res.Status)" -ne "Success") { $out.ok = $false; $out.error = "$($res.Status)"; $out.errorType = "TETHERING" }
            Read-Status $mgr
        }
        "stop" {
            $mgr = Get-Manager
            $res = Await-Op ($mgr.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
            $out.result = "$($res.Status)"
            if ("$($res.Status)" -ne "Success") { $out.ok = $false; $out.error = "$($res.Status)"; $out.errorType = "TETHERING" }
            Read-Status $mgr
        }
        default {
            $out.ok = $false
            $out.error = "UNKNOWN_ACTION:$Action"
            $out.errorType = "USAGE"
        }
    }
}
catch {
    $out.ok = $false
    $msg = "$($_.Exception.Message)"
    $out.error = $msg
    if ($msg -match "NO_INTERNET_PROFILE") { $out.errorType = "NO_INTERNET_PROFILE" }
    elseif ($msg -match "Unable to find type") { $out.errorType = "API_UNAVAILABLE" }
    else { $out.errorType = "RUNTIME" }
}

$out | ConvertTo-Json -Depth 6 -Compress
