# Security Policy

## Supported versions

Only the latest release on the `main` branch receives security fixes.

## Reporting a vulnerability

Do not open a public Issue for vulnerabilities. Use GitHub's **Report a vulnerability** private advisory feature. Include affected version, reproduction steps, impact, logs with personal data removed, and a suggested mitigation if available.

Please allow a reasonable period for triage before public disclosure.

## Deployment warning

The PTZ nodes expose unauthenticated HTTP control endpoints on ports `8723–8727`. They are intended for a trusted local network only. Do not port-forward them to the Internet. Use Windows Firewall, VLAN isolation, or a VPN when remote access is required.
