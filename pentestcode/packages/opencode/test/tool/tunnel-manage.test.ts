import { describe, expect, test } from "bun:test"
import { buildTunnelCommand } from "../../src/tool/tunnel-manage"
import { shq } from "../../src/tool/cred-spray"

describe("#22 command injection prevention", () => {
  test("credential value with shell metacharacters is safely quoted", () => {
    const malicious = "pw'; touch /tmp/pwned; #"
    const out = buildTunnelCommand(
      "ssh_dynamic",
      "10.0.0.5",
      { user: "svc", value: malicious, isKey: false },
      9050,
      "<ATTACKER_IP>",
      "chisel",
    ).join("\n")
    expect(out).toContain(`sshpass -p ${shq(malicious)}`)
    expect(out).not.toMatch(/sshpass -p 'pw';/)
  })

  test("username with shell metacharacters is safely quoted", () => {
    const malicious = "svc; touch /tmp/pwned; #"
    const out = buildTunnelCommand(
      "ssh_dynamic",
      "10.0.0.5",
      { user: malicious, value: "pw", isKey: false },
      9050,
      "<ATTACKER_IP>",
      "chisel",
    ).join("\n")
    expect(out).toContain(`${shq(malicious)}@10.0.0.5`)
  })

  test("host_ip with shell metacharacters is rejected", () => {
    expect(() =>
      buildTunnelCommand("ssh_dynamic", "10.0.0.5; touch /tmp/pwned", undefined, 9050, "<ATTACKER_IP>", "chisel"),
    ).toThrow("Invalid host/IP")
  })

  test("binary name with shell metacharacters is rejected", () => {
    expect(() =>
      buildTunnelCommand("chisel", "10.0.0.5", undefined, 9050, "10.10.14.9", "chisel; rm -rf /"),
    ).toThrow("Invalid binary name")
  })

  test("ssh key path with shell metacharacters is safely quoted", () => {
    const malicious = "/tmp/key'; cat /etc/shadow; #"
    const out = buildTunnelCommand(
      "ssh_dynamic",
      "10.0.0.5",
      { user: "root", value: malicious, isKey: true },
      9050,
      "<ATTACKER_IP>",
      "chisel",
    ).join("\n")
    expect(out).toContain(`-i ${shq(malicious)}`)
  })

  test("invalid port is rejected", () => {
    expect(() =>
      buildTunnelCommand("ssh_dynamic", "10.0.0.5", undefined, 99999, "<ATTACKER_IP>", "chisel"),
    ).toThrow("Invalid port")
  })
})

// #2b: the reliable pivot recipe. A chisel plan must be COMPLETE and runnable —
// attacker IP filled (no placeholder), binary staged through a constrained RCE, a
// reverse SOCKS on our port, and a verify step — otherwise the pivot dies in the field.
describe("#2b buildTunnelCommand — reliable pivot recipe", () => {
  test("chisel: fills attacker IP, stages the binary, exposes SOCKS, verifies", () => {
    const out = buildTunnelCommand("chisel", "172.50.1.18", undefined, 9050, "10.10.14.9", "chisel").join("\n")
    expect(out).toContain("10.10.14.9:8000") // attacker IP filled into the reverse server
    expect(out).not.toContain("YOUR_IP") // no leftover placeholder
    expect(out).toContain("R:9050:socks") // reverse SOCKS bound on our local port
    expect(out).toContain("base64 -d") // staged base64-chunked transfer present
    expect(out).toContain("proxychains") // verify step present
  })

  test("chisel with unknown attacker IP surfaces the placeholder (caller warns)", () => {
    const out = buildTunnelCommand("chisel", "172.50.1.18", undefined, 9050, "<ATTACKER_IP>", "chisel").join("\n")
    expect(out).toContain("<ATTACKER_IP>:8000")
  })

  test("ssh_dynamic: emits a -D SOCKS proxy and a verify", () => {
    const out = buildTunnelCommand(
      "ssh_dynamic",
      "172.50.1.18",
      { user: "root", value: "pw", isKey: false },
      1080,
      "<ATTACKER_IP>",
      "chisel",
    ).join("\n")
    expect(out).toContain("-D 1080")
    expect(out).toContain("'root'@172.50.1.18")
    expect(out).toContain("proxychains")
  })
})
