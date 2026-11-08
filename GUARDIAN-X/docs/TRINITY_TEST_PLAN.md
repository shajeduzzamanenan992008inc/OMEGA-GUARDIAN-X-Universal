# Trinity reference-repository test plan

These are the first three reference repositories for OGX adapter development:

1. Brahma-Ai-Evo — `https://github.com/titechprabhasolutions/Brahma-Ai-Evo.git`
2. Mark-LVI — `https://github.com/FatihMakes/Mark-LVI.git`
3. OMEGA-NEXUS-PRIME — `https://github.com/shajeduzzamanenan992008inc/OMEGA-NEXUS-PRIME.git`

## Important boundaries

- Run scans only on copies/repositories the operator is authorized to inspect.
- OMEGA-NEXUS-PRIME may require authenticated Git access.
- Brahma-Ai-Evo's repository license must be reviewed before any redistribution or commercial bundling of its source. OGX is an independent analyzer; this project does not copy its implementation.
- The 0.4.0a1 scanner is static and heuristic. Do not treat its reports as complete audits or proof of security.

## Status

These reference repositories have **not** been fully scanned or compatibility-certified by 0.4.0a1. The following sequence is a proposed test plan for local clones the operator is authorized to inspect. The Brahma-Ai-Evo restrictive license must be respected; no source code is to be bundled into OGX.

## Windows PowerShell sequence

After cloning/locating the three repositories, install the OGX wheel and use the installed module:

```powershell
py -m pip install .\omega_guardian_x-0.4.0a1-py3-none-any.whl
py -m ogx --version

# One repository at a time
py -m ogx start "D:\\Repos\\Brahma-Ai-Evo"
py -m ogx start "D:\\Repos\\Mark-LVI"
py -m ogx start "D:\\Repos\\OMEGA-NEXUS-PRIME"
```

Or extract the source package and use the included sequential helper after installing OGX:

```powershell
.\scripts\scan-many.ps1 -RepositoryPaths @( 
  "D:\\Repos\\Brahma-Ai-Evo", 
  "D:\\Repos\\Mark-LVI", 
  "D:\\Repos\\OMEGA-NEXUS-PRIME" 
)
```

Review and optionally apply the project-root agent/control integration sequentially to all repositories (the script requests an individual confirmation for each):

```powershell
.\scripts\integrate-many.ps1 -RepositoryPaths @(
  "D:\Repos\Brahma-Ai-Evo",
  "D:\Repos\Mark-LVI",
  "D:\Repos\OMEGA-NEXUS-PRIME"
) -Apply
```

Or use `ogx integrate plan PATH` and `ogx integrate apply PATH --confirm` manually for each one. The integration adds `.github/` governance files and `OMEGA GUARDIAN X/` only where absent; it preserves existing root instructions and Dependabot configuration. Normal scans remain read-only. Reports are written outside targets under `%LOCALAPPDATA%\OMEGA-GUARDIAN-X\projects\...\reports`. Use one repository at a time and review detected stack, skipped files, capabilities, warnings, and false positives before relying on findings.
