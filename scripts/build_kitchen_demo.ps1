[CmdletBinding()]
param(
    [ValidateSet('Verify','Build','Package')][string]$Phase='Verify',
    [ValidateSet('Demo','Research')][string]$Variant='Demo',
    [Parameter(Mandatory)][string]$Output,
    [Parameter(Mandatory)][string]$Temp,
    [string]$Export,
    [string]$ExportVerification,
    [ValidateSet('KitchenDemoV3/catalog','KitchenDemoV3Side20261022/catalog','KitchenDemoV3Clear20261023/catalog')][string]$CatalogResource='KitchenDemoV3/catalog',
    [switch]$ContinuousPlayback,
    [string]$ResearchHost,
    [string]$GradleHome='D:/KDGC2202',
    [int]$ResearchPlannerPort=11031,
    [int]$ResearchWorldPort=11032,
    [string]$UnityExe='C:/Program Files/Unity/Hub/Editor/6000.0.66f2/Editor/Unity.exe'
)
$ErrorActionPreference='Stop'
$root=(git rev-parse --show-toplevel).Trim()
$project=Join-Path $root 'm7_unity6000'
if($ContinuousPlayback -and ($Phase -ne 'Verify' -or $Variant -ne 'Demo')){throw 'ContinuousPlayback is a Demo Verify-only software check'}
$Output=[IO.Path]::GetFullPath($Output)
if($Output -notmatch '[\\/]fullops_20261021[\\/]demo_[^\\/]+$'){throw 'Output must be a new dedicated fullops/demo_* directory'}
if(Test-Path -LiteralPath $Output){throw 'Never overwrite a prior Demo verification/build attempt'}
if($Temp -notmatch '^D:/[A-Za-z0-9_]+$' -or (Test-Path -LiteralPath $Temp)){throw 'Use a new short dedicated D temporary directory'}
if($Phase -eq 'Build' -and ($Export -notmatch '^D:/[A-Za-z0-9_]+$' -or (Test-Path -LiteralPath $Export))){throw 'Use a new short D Gradle export'}
if($Phase -eq 'Package' -and ($Export -notmatch '^D:/[A-Za-z0-9_]+$' -or -not (Test-Path -LiteralPath $Export) -or -not (Test-Path -LiteralPath $ExportVerification -PathType Leaf))){throw 'Package requires an existing official export and its prior verification report'}
if((Get-PSDrive C).Free -lt 5GB -or (Get-PSDrive D).Free -lt 15GB){throw 'Insufficient disk capacity'}
if($Phase -ne 'Package' -and @(Get-Process Unity -ErrorAction SilentlyContinue).Count -ne 0){throw 'Unity already running; one Editor only'}
if($Phase -ne 'Verify' -and ($GradleHome -notmatch '^D:/' -or -not (Test-Path -LiteralPath (Join-Path $GradleHome 'caches')))){throw 'Verified existing dependencies must be staged in D GradleHome'}
$assets=Join-Path $project ('Assets/Resources/'+($CatalogResource -replace '/catalog$',''))
if(-not (Test-Path -LiteralPath (Join-Path $assets 'catalog.json'))){throw 'Export and audit the immutable Demo records first'}
$catalog=Get-Content -Raw -LiteralPath (Join-Path $assets 'catalog.json') | ConvertFrom-Json
$original='2ff69af80d5c6fc0724382dcd478ffa2e0699a227153c46d263a368c5b246155'
$legacy=$catalog.schema -eq 1 -and $catalog.control_hash -ceq $original
$side=$catalog.schema -eq 2 -and $catalog.control_hash -ceq 'PER_EPISODE_RECORDED_CONTROL' -and $catalog.episodes.Count -eq 2 -and $catalog.episodes[0].id -ceq 'apple' -and $catalog.episodes[0].control_hash -ceq 'b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2' -and $catalog.episodes[1].id -ceq 'burger' -and $catalog.episodes[1].control_hash -ceq $original
if(-not ($legacy -or $side)){throw 'Not a supported versioned recorded control identity'}
$sources=@('Assets/BCI/Kitchen/KitchenDemoPlayback.cs','Assets/BCI/Kitchen/KitchenDemoUI.cs','Assets/BCI/Kitchen/KitchenVrPanel.cs','Assets/Resources/KitchenWorldPanel.shader',
    'Assets/BCI/Kitchen/KitchenResearchControls.cs',
    'Assets/Editor/KitchenDemoResearchBuild.cs','Assets/Editor/KitchenResearchEditorProbe.cs','Assets/Editor/KitchenDemoContinuousProbe.cs','Assets/BCI/Kitchen/KitchenPlatformArt.cs',
    'Assets/BCI/Kitchen/KitchenSceneMirror.cs','Assets/BCI/Kitchen/KitchenPlatformView.cs','Assets/Editor/KitchenPlatformBuild.cs')
$sourceHashes=@{}
foreach($relative in $sources){$sourceHashes[$relative]=(Get-FileHash -LiteralPath (Join-Path $project $relative)).Hash.ToLowerInvariant()}
$catalogHash=(Get-FileHash -LiteralPath (Join-Path $assets 'catalog.json')).Hash.ToLowerInvariant()
$matchedEditorVerification=$null
if($Phase -ne 'Verify'){
    $verifyPattern=if($Variant -eq 'Research'){'demo_research_pc_*'}else{'demo_pc_*'}
    $verifyStatus=if($Variant -eq 'Research'){'PC_RESEARCH_EDITOR_INTERACTION_PASS'}else{'PC_EDITOR_INTERACTION_PASS'}
    foreach($directory in Get-ChildItem -LiteralPath (Split-Path -Parent $Output) -Directory -Filter $verifyPattern){
        $path=Join-Path $directory.FullName 'verification.json'
        if(-not (Test-Path -LiteralPath $path -PathType Leaf)){continue}
        try{$candidate=Get-Content -Raw -LiteralPath $path | ConvertFrom-Json}catch{continue}
        if($candidate.status -cne $verifyStatus -or $candidate.variant -cne $Variant -or
            $candidate.phase -cne 'Verify' -or $candidate.interaction.status -cne $verifyStatus -or
            $candidate.catalog_sha256 -cne $catalogHash){continue}
        $match=$true
        foreach($relative in $sources){if($candidate.source_hashes.$relative -cne $sourceHashes[$relative]){$match=$false;break}}
        if($match){$matchedEditorVerification=$path;break}
    }
    if($null -eq $matchedEditorVerification){throw 'No real Editor PASS matches the current variant/source/catalog fingerprint'}
}
New-Item -ItemType Directory -Path $Output,$Temp | Out-Null
$protected=Join-Path $Output 'protected_before'
New-Item -ItemType Directory -Path $protected | Out-Null
$files=@()
foreach($relative in $(if($Phase -eq 'Package'){@()}else{@('ProjectSettings','UserSettings','Assets/XR','Assets/Scenes')})){
    $path=Join-Path $project $relative
    if(Test-Path -LiteralPath $path){$files+=@(Get-ChildItem -LiteralPath $path -Recurse -File)}
}
if($Phase -ne 'Package'){$files+=@(Get-ChildItem -LiteralPath (Join-Path $project 'Assets/Resources') -File)}
$snapshots=@();$i=0
foreach($file in $files){
    $backup=Join-Path $protected ($i.ToString('D4')+'.snapshot')
    Copy-Item -LiteralPath $file.FullName -Destination $backup
    $snapshots+=@{path=$file.FullName;backup=$backup;sha256=(Get-FileHash -LiteralPath $file.FullName).Hash};$i++
}
$result=[ordered]@{status='RUNNING';phase=$Phase;variant=$Variant;start_utc=[DateTime]::UtcNow.ToString('o');unity=$UnityExe;
    root=$root;export=$Export;temp=$Temp;catalog_resource=$CatalogResource;catalog_sha256=$catalogHash;matched_editor_verification=$matchedEditorVerification;
    recorded_control_hash=$catalog.control_hash;source_hashes=$sourceHashes;physics='NOT_EXECUTED';quest='HARDWARE_REQUIRED';device_operation='NONE_EXPLICIT'}
$envNames=@('TEMP','TMP','GRADLE_USER_HOME','KITCHEN_DEMO_OUTPUT','KITCHEN_DEMO_EXPORT','KITCHEN_PLATFORM_GRADLE_EXPORT','KITCHEN_PLATFORM_OUTPUT',
    'KITCHEN_RESEARCH_HOST','KITCHEN_RESEARCH_PLANNER_PORT','KITCHEN_RESEARCH_WORLD_PORT','KITCHEN_DEMO_CATALOG')
$prior=@{};foreach($name in $envNames){$prior[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$env:TEMP=$Temp;$env:TMP=$Temp;$env:KITCHEN_DEMO_OUTPUT=$Output;$env:KITCHEN_DEMO_EXPORT=$Export
$env:GRADLE_USER_HOME=$GradleHome
$env:KITCHEN_PLATFORM_GRADLE_EXPORT=$null;$env:KITCHEN_PLATFORM_OUTPUT=$null
$env:KITCHEN_DEMO_CATALOG=$CatalogResource
$env:KITCHEN_RESEARCH_HOST=$ResearchHost;$env:KITCHEN_RESEARCH_PLANNER_PORT=[string]$ResearchPlannerPort;$env:KITCHEN_RESEARCH_WORLD_PORT=[string]$ResearchWorldPort
$android=Join-Path (Split-Path -Parent $UnityExe) 'Data/PlaybackEngines/AndroidPlayer'
$java=Join-Path $android 'OpenJDK/bin/java.exe'
$owned=@{}
function WaitOwned($process,[int]$seconds){
    $owned[$process.Id]=$process.StartTime
    $deadline=[DateTime]::UtcNow.AddSeconds($seconds)
    while(-not $process.HasExited){
        if([DateTime]::UtcNow -gt $deadline){throw ('Bounded process timeout '+$process.Id)}
        if((Get-PSDrive C).Free -lt 4GB -or (Get-PSDrive D).Free -lt 10GB){throw 'Capacity safety stop'}
        $process.WaitForExit(1000) | Out-Null
        $process.Refresh()
    }
    $process.WaitForExit();return $process.ExitCode
}
try{
    if($Phase -eq 'Package'){
        $priorExport=Get-Content -Raw -LiteralPath $ExportVerification | ConvertFrom-Json
        if($priorExport.unity_exit_code -ne 0 -or $priorExport.variant -cne $Variant -or
            $priorExport.catalog_sha256 -cne $result.catalog_sha256 -or
            [IO.Path]::GetFullPath($priorExport.export) -cne [IO.Path]::GetFullPath($Export)){throw 'Prior official export binding mismatch'}
        foreach($relative in $sources){if($priorExport.source_hashes.$relative -cne $sourceHashes[$relative]){throw ('Source differs from official export: '+$relative)}}
        $evidence=$priorExport.unity_export
        $result.export_verification=$ExportVerification;$result.unity_export=$evidence
    }else{
    $method=if($ContinuousPlayback){'KitchenDemoContinuousProbe.VerifyContinuous'}elseif($Phase -eq 'Verify' -and $Variant -eq 'Research'){'KitchenResearchEditorProbe.VerifyResearchInteractive'}elseif($Phase -eq 'Verify'){'KitchenDemoResearchBuild.VerifyDemoInteractive'}elseif($Variant -eq 'Research'){'KitchenDemoResearchBuild.BuildResearchApkBatch'}else{'KitchenDemoResearchBuild.BuildDemoApkBatch'}
    $arguments=@('-timestamps','-buildTarget','Android','-projectPath',('"'+$project+'"'),'-executeMethod',
        ('BCIIntelligentRobot.Kitchen.Editor.'+$method),'-logFile',('"'+(Join-Path $Output 'editor.log')+'"'))
    if($Phase -eq 'Build'){$arguments=@('-batchmode')+$arguments}
    $result.unity_arguments=$arguments
    $unity=Start-Process -FilePath $UnityExe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $result.unity_pid=$unity.Id
    $unityBudget=if($ContinuousPlayback){640}else{420}
    $result.unity_exit_code=WaitOwned $unity $unityBudget
    if($result.unity_exit_code -ne 0){throw 'Real Unity entry failed; inspect editor.log'}
    }
    if($Phase -eq 'Verify'){
        $evidence=Get-Content -Raw -LiteralPath (Join-Path $Output 'editor_interaction_report.json') | ConvertFrom-Json
        $expected=if($ContinuousPlayback){'PC_DEMO_CONTINUOUS_PLAYBACK_PASS'}elseif($Variant -eq 'Research'){'PC_RESEARCH_EDITOR_INTERACTION_PASS'}else{'PC_EDITOR_INTERACTION_PASS'}
        if($evidence.status -cne $expected){throw 'Real variant PC interaction regression did not pass'}
        $result.status=$expected;$result.interaction=$evidence
    }else{
        if($Phase -ne 'Package'){$evidence=Get-Content -Raw -LiteralPath (Join-Path $Output 'demo_build_report.json') | ConvertFrom-Json}
        $package=if($Variant -eq 'Research'){'org.bci.kitchenresearch.v3'}else{'org.bci.kitchendemo.v3'}
        $label=if($Variant -eq 'Research'){'Kitchen Research'}else{'Kitchen Demo'}
        $expected=if($Variant -eq 'Research'){'RESEARCH_UNITY_EXPORT_PASS'}else{'DEMO_UNITY_EXPORT_PASS'}
        if($evidence.status -cne $expected -or -not $evidence.settings_restored -or
            $evidence.package -cne $package -or $evidence.catalog_sha256 -cne $result.catalog_sha256){throw 'Official variant export identity gate failed'}
        $result.unity_export=$evidence
        $launcher=Join-Path $android 'Tools/gradle/lib/gradle-launcher-8.13.jar'
        $arguments=@('-classpath',('"'+$launcher+'"'),'org.gradle.launcher.GradleMain','--offline','--no-daemon','--console=plain','-p',('"'+$Export+'"'),':launcher:assembleRelease')
        $gradle=Start-Process -FilePath $java -ArgumentList $arguments -WorkingDirectory $Export -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Output 'gradle.stdout.log') -RedirectStandardError (Join-Path $Output 'gradle.stderr.log')
        $result.gradle_pid=$gradle.Id;$result.gradle_exit_code=WaitOwned $gradle 1200
        if($result.gradle_exit_code -ne 0){throw 'Offline Gradle failed'}
        $apk=Join-Path $Output ($label.Replace(' ','')+'.apk')
        Copy-Item -LiteralPath (Join-Path $Export 'launcher/build/outputs/apk/release/launcher-release.apk') -Destination $apk
        $signer=Join-Path $android 'SDK/build-tools/36.0.0/lib/apksigner.jar'
        $sign=Start-Process -FilePath $java -ArgumentList @('-jar',('"'+$signer+'"'),'verify','--verbose','--print-certs',('"'+$apk+'"')) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Output 'signature.txt') -RedirectStandardError (Join-Path $Output 'signature.stderr.txt')
        $result.signature_exit_code=WaitOwned $sign 30
        $aapt=Start-Process -FilePath (Join-Path $android 'SDK/build-tools/36.0.0/aapt2.exe') -ArgumentList @('dump','badging',('"'+$apk+'"')) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Output 'badging.txt') -RedirectStandardError (Join-Path $Output 'badging.stderr.txt')
        $result.badging_exit_code=WaitOwned $aapt 30
        $badging=Get-Content -Raw -LiteralPath (Join-Path $Output 'badging.txt')
        if($result.signature_exit_code -ne 0 -or $result.badging_exit_code -ne 0 -or $badging -notmatch ([regex]::Escape("package: name='"+$package+"'")) -or $badging -notmatch ([regex]::Escape("application-label:'"+$label+"'"))){throw 'Signed APK actual identity/label audit failed'}
        $result.apk=$apk;$result.apk_bytes=(Get-Item -LiteralPath $apk).Length;$result.apk_sha256=(Get-FileHash -LiteralPath $apk).Hash.ToLowerInvariant()
        $result.status=$Variant.ToUpperInvariant()+'_SIGNED_APK_BUILD_PASS_NOT_QUEST'
    }
    foreach($relative in $sources){if((Get-FileHash -LiteralPath (Join-Path $project $relative)).Hash.ToLowerInvariant() -cne $sourceHashes[$relative]){throw ('Source changed during build: '+$relative)}}
}catch{
    $result.status='FAIL';$result.error=$_.Exception.ToString();throw
}finally{
    foreach($pidValue in @($owned.Keys)){
        $process=Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if($null -ne $process -and $process.StartTime -eq $owned[$pidValue]){Stop-Process -InputObject $process -Force}
    }
    $restored=@()
    foreach($snapshot in $snapshots){
        if(-not (Test-Path -LiteralPath $snapshot.path) -or (Get-FileHash -LiteralPath $snapshot.path).Hash -cne $snapshot.sha256){
            Copy-Item -LiteralPath $snapshot.backup -Destination $snapshot.path;$restored+=$snapshot.path
        }
        if((Get-FileHash -LiteralPath $snapshot.path).Hash -cne $snapshot.sha256){throw 'Protected persistent state restoration failed'}
    }
    foreach($name in $envNames){[Environment]::SetEnvironmentVariable($name,$prior[$name],'Process')}
    $result.restored_protected_files=$restored;$result.protected_files_verified=$snapshots.Count
    $result.end_utc=[DateTime]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $Output 'verification.json') -Encoding utf8
}
$result | ConvertTo-Json -Depth 4
