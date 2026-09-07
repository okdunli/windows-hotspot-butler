param($Action, $Public, $Private)
$ErrorActionPreference = 'Stop'

function Find-Conn($name) {
    $share = New-Object -ComObject HNetCfg.HNetShare
    foreach ($c in $share.EnumEveryConnection) {
        $props = $share.NetConnectionProps($c)
        if ($props.Name -eq $name) { return $c }
    }
    return $null
}

try {
    $share = New-Object -ComObject HNetCfg.HNetShare
    if ($Action -eq 'Enable') {
        $pub = Find-Conn $Public
        $priv = Find-Conn $Private
        if (-not $pub) { throw "public adapter not found: $Public" }
        if (-not $priv) { throw "private adapter not found: $Private" }
        $pubCfg = $share.INetSharingConfigurationForINetConnection.Invoke($pub)
        $privCfg = $share.INetSharingConfigurationForINetConnection.Invoke($priv)
        # 0 = PUBLIC(共享出去), 1 = PRIVATE(被共享)
        try { $pubCfg.EnableSharing(0) } catch { }
        try { $privCfg.EnableSharing(1) } catch { }
        ConvertTo-Json -Compress @{ok = $true; message = "shared $Public -> $Private" }
    } else {
        $priv = Find-Conn $Private
        if ($priv) {
            $privCfg = $share.INetSharingConfigurationForINetConnection.Invoke($priv)
            try { $privCfg.DisableSharing() } catch { }
        }
        ConvertTo-Json -Compress @{ok = $true; message = "sharing disabled" }
    }
} catch {
    ConvertTo-Json -Compress @{ok = $false; error = $_.Exception.Message }
}
