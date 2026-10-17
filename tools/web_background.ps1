$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $teoRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $teoStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow = [uint16]0
        CreateFlags = [uint32]0x09000000 # CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
    }
    $teoResult = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = [string]$teoRequest.command
        CurrentDirectory = [string]$teoRequest.directory
        ProcessStartupInformation = $teoStartup
    }
    @{ code = [int]$teoResult.ReturnValue; pid = [int]$teoResult.ProcessId } | ConvertTo-Json -Compress
} catch {
    # Keep request and any provider error private, including command arguments.
    '{"code":-1,"pid":0}'
    exit 1
}
