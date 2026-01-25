# Incident Response Runbook - Animica CEX Platform

**Version:** 1.0 | **Last Updated:** January 2025 | **Owner:** Security Team

---

## Executive Summary

This runbook provides comprehensive step-by-step procedures for responding to security incidents affecting the Animica CEX platform. It defines incident classification, response procedures for common scenarios, escalation paths, communication templates, and post-incident review processes suitable for an exchange MVP seeking regulatory approval.

**PURPOSE:** Enable rapid, effective response to security incidents to minimize impact, protect customer assets, and maintain regulatory compliance.

**SCOPE:** All security incidents including data breaches, DDoS attacks, withdrawal fraud, system compromises, insider threats, and third-party vendor incidents.

---

## 1. Incident Classification

### 1.1 Severity Levels

| Severity | Definition | Response Time | Escalation | Examples |
|----------|------------|---------------|------------|----------|
| **P0 - CRITICAL** | Active threat to customer funds or data; massive service outage | Immediate (15 min) | CISO, CEO, Board (if funds at risk) | Hot wallet compromise, database breach, ransomware, active funds theft |
| **P1 - HIGH** | Significant security issue with limited service impact | 1 hour | CISO, CTO | Attempted intrusion, DDoS attack, suspicious admin activity, vulnerability exploit |
| **P2 - MEDIUM** | Security concern requiring investigation with minor impact | 4 hours | Security Team Lead | Unusual transaction patterns, failed login spike, vulnerability disclosure |
| **P3 - LOW** | Potential security issue with no immediate impact | 24 hours | Security Engineer (monitor) | Port scan activity, outdated dependency, minor policy violation |

### 1.2 Incident Categories

**1. UNAUTHORIZED ACCESS:** Account takeover (user/admin), API key compromise, database breach, system intrusion  
**2. SERVICE DISRUPTION:** DDoS attack, ransomware, outage (security-related), data corruption  
**3. FRAUD & FINANCIAL:** Withdrawal fraud, market manipulation, money laundering, insider trading  
**4. DATA BREACH:** Customer PII exposure, KYC document leak, API key leak, source code exposure  
**5. INSIDER THREAT:** Privilege abuse, data exfiltration, sabotage, policy violations  
**6. THIRD-PARTY:** BitGo compromise, AWS breach, supply chain attack, vendor incident

### 1.3 Initial Triage Questions

**ASK:** What happened? When? Systems affected? Still ongoing? Customer impact? Who discovered?  
**ASSESS:** Severity, containment status, evidence preservation, external help needed

---

## 2. Incident Response Procedures

### 2.1 P0 - Hot Wallet Compromise

**SCENARIO:** Unauthorized transactions detected from BitGo hot wallet

**IMMEDIATE (0-15 min):**
1. **Freeze withdrawals:** `systemctl stop withdrawals-service` or admin API pause
2. **Notify BitGo hotline:** +1-XXX-XXX-XXXX, request wallet freeze
3. **Isolate systems:** Revoke API keys, apply security group lockdown
4. **Assess damage:** Query unauthorized withdrawals, calculate loss
5. **Executive notification:** CISO immediate, CEO if >$100k

**NEXT 1 HOUR:**
6. **Preserve evidence:** VM snapshots, export audit logs, preserve network logs
7. **Verify containment:** Confirm withdrawals paused, BitGo frozen, no ongoing access
8. **Initial RCA:** Attack vector, credentials used, timeline

**NEXT 4 HOURS:**
9. **Eradicate:** Patch vulnerability, rotate all secrets, remove backdoors
10. **Plan recovery:** New wallets or reinstate with monitoring

**NEXT 24 HOURS:**
11. **Execute recovery:** Deploy/reinstate wallets, gradual withdrawal enablement
12. **Regulatory:** File SAR (FinCEN), notify state regulators
13. **Schedule PIR**

### 2.2 P0 - Database Breach / Data Exfiltration

**SCENARIO:** Unauthorized access to production database detected

**IMMEDIATE:**
1. **Identify scope:** Tables accessed, data exfiltrated, timeline
2. **Isolate DB:** Revoke access, change master password, revoke service accounts
3. **Kill sessions:** Terminate suspicious DB connections
4. **Assess exposure:** PII, KYC documents, passwords, API keys
5. **Containment:** Block attacker IP, enable audit logging, snapshot for forensics

**NEXT 1 HOUR:**
6. **Forensics:** Analyze query/auth/network logs, determine attack vector
7. **Eradicate:** Patch vulnerability, rotate all DB creds, deploy additional controls

**NEXT 24 HOURS:**
8. **Customer notification (if PII):** Email template, website notice, regulatory (GDPR 72h, CCPA, state laws)
9. **Recovery:** Restore with new creds, enhanced logging
10. **Credit monitoring (if KYC):** Offer 1-year free service to affected users

### 2.3 P1 - DDoS Attack

**SCENARIO:** Massive traffic spike, API unresponsive

**IMMEDIATE:**
1. **Confirm DDoS:** Traffic graphs, source identification, distinguish from legitimate surge
2. **Activate mitigation:** Cloudflare "Under Attack" mode or AWS WAF rate-based rules
3. **App rate limiting:** Tighten Redis limits temporarily
4. **Geo-block if localized:** Block attack source countries
5. **Communicate:** Status page update, customer support brief

**NEXT 1-4 HOURS:**
6. **Analyze:** Volumetric vs. app-layer vs. protocol abuse
7. **Adaptive response:** CAPTCHA challenges, evolving WAF rules, ISP coordination
8. **Recovery:** Gradually ease limits, monitor for recurring waves

### 2.4 P1 - Withdrawal Fraud

**SCENARIO:** Multiple fraudulent withdrawals detected (account takeovers)

**IMMEDIATE:**
1. **Suspend affected accounts**
2. **Halt pending withdrawals** for those accounts
3. **Assess pattern:** New addresses, unusual geo, password changes, 2FA resets
4. **Blockchain analysis:** Chainalysis/Elliptic/TRM on destination addresses
5. **Contact users:** Email/call to verify identity, secure account

**NEXT 4 HOURS:**
6. **Determine vector:** Phishing, credential stuffing, API key leak, insider
7. **Contain:** Force 2FA re-enrollment, password reset, disable API withdrawals, enhance risk scoring
8. **Recovery attempts:** Contact recipient exchanges, file police report, file SAR

**POST-INCIDENT:** Mandatory 2FA (no grace), withdrawal whitelist, 24h delay for new addresses

### 2.5 P2 - Vulnerability Disclosure

**SCENARIO:** Security researcher reports critical vulnerability

**IMMEDIATE:**
1. **Acknowledge:** Email researcher within 1 hour
2. **Triage:** CVSS score, affected systems, reproduceability
3. **Develop patch:** Assign to engineering, target 24h (critical) or 72h (high)
4. **Deploy patch:** Emergency deployment if critical, monitor post-deployment
5. **Coordinate disclosure:** Agree on timeline (typically 90 days), offer bug bounty

**NEXT 7-90 DAYS:**
6. **Public disclosure:** Post security advisory, credit researcher
7. **Retrospective:** How introduced, process improvements

---

## 3. Escalation Paths

### 3.1 Escalation Matrix

| Severity | First Responder | 15 min | 1 hour | 4 hours | 24 hours |
|----------|----------------|--------|--------|---------|----------|
| **P0 - CRITICAL** | On-call Engineer | CISO, CTO | CEO, Legal | Board (if $1M+ loss) | Regulators (if required) |
| **P1 - HIGH** | Security Team Lead | CISO | CTO | CEO (if ongoing) | - |
| **P2 - MEDIUM** | Security Engineer | Team Lead | CISO (if escalates) | - | - |
| **P3 - LOW** | Security Engineer | - | Team Lead (if needed) | - | - |

### 3.2 Contact Information

**INTERNAL:**
- On-call Engineer: PagerDuty escalation / Slack #security-incidents
- Security Team Lead: security-lead@animica.exchange, +1-XXX-XXX-XXXX
- CISO: ciso@animica.exchange, +1-XXX-XXX-XXXX
- CTO: cto@animica.exchange, +1-XXX-XXX-XXXX
- CEO: ceo@animica.exchange, +1-XXX-XXX-XXXX
- Legal Counsel: legal@animica.exchange, +1-XXX-XXX-XXXX

**EXTERNAL:**
- BitGo 24/7 Hotline: +1-XXX-XXX-XXXX
- AWS Support (Premium): +1-XXX-XXX-XXXX
- Forensic Firm (Mandiant/CrowdStrike): Pre-arranged contract, +1-XXX-XXX-XXXX
- FBI Cyber Division: +1-XXX-XXX-XXXX
- FinCEN Hotline: +1-XXX-XXX-XXXX

---

## 4. Communication Templates

### 4.1 Data Breach Notification (Email to Customers)

**SUBJECT:** Important Security Notice - Action Required

Dear [Customer Name],

We are writing to inform you of a security incident that may have affected your account information.

**WHAT HAPPENED:** On [Date], we discovered that an unauthorized party accessed our systems and may have obtained certain customer information, including [specific data types].

**INFORMATION INVOLVED:** [List data types]. IMPORTANT: Your financial information (credit cards, bank accounts) was NOT compromised.

**OUR ACTIONS:** Secured systems immediately, engaged cybersecurity experts, notified law enforcement, implemented additional measures, offering 12-month credit monitoring.

**YOUR ACTIONS:**
1. Change password immediately: [link]
2. Enable 2FA: [link]
3. Monitor account for suspicious activity
4. Beware of phishing attempts
5. Enroll in free credit monitoring: [link]

**QUESTIONS?** incident-support@animica.exchange | +1-XXX-XXX-XXXX (24/7)

We sincerely apologize and are committed to regaining your trust.

Sincerely, [CEO Name]  
CEO, Animica Exchange

Incident Reference: INC-2025-001

### 4.2 Status Page Updates

**INVESTIGATING:**
```
[2025-01-25 14:32 UTC] INVESTIGATING
We are experiencing technical difficulties affecting [API/withdrawals/login/trading]. 
Investigating. Updates every 30 minutes.
Status: Degraded Performance
```

**IDENTIFIED:**
```
[2025-01-25 15:05 UTC] IDENTIFIED
Root cause identified as [brief description]. 
Implementing fix. Expected resolution: [time estimate].
Status: Degraded Performance
```

**RESOLVED:**
```
[2025-01-25 16:30 UTC] RESOLVED
All systems now fully operational. 
Post-mortem within 5 business days.
Status: Operational
```

### 4.3 Internal Slack Notification

```
@here SECURITY INCIDENT - [P0/P1/P2]

Incident ID: INC-2025-001
Severity: [P0 - CRITICAL]
Category: [Unauthorized Access/etc.]

SUMMARY: [Brief description]
AFFECTED: [Systems list]
STATUS: [Ongoing/Contained/Resolved]
ACTIONS: [List of actions taken]
NEXT: [Next steps]

COMMANDER: @[name]
WAR ROOM: #incident-2025-001

DO NOT DISCUSS PUBLICLY.
```

---

## 5. Emergency Contacts & Procedures

### 5.1 War Room Protocol

**WHEN:** Any P0, P1 >2h, multi-service outage, cross-team coordination

**SETUP:**
1. Create Slack channel: `#incident-YYYY-NNN`
2. Bridge to Zoom call (standing link)
3. Assign roles: Incident Commander, Comms Lead, Technical Lead, Scribe

**ETIQUETTE:** Stay on-topic, update every 15-30min, use threads, tag [DECISION] and [ACTION]

### 5.2 Break-Glass Procedures

**WHEN:** Emergency access when primary auth unavailable (IC authorization required)

**ACCESS:** Emergency admin account (YubiKey + CISO approval code + justification)

**POST:** Revoke immediately after incident, generate new creds, file report, CISO review 24h

### 5.3 Emergency Shutdown

**ONLY IF:** Active ransomware, uncontained exfiltration, cascading failure  
**AUTH:** CISO or CEO only  
**STEPS:** Notify users, cancel orders, shutdown services, isolate network, snapshot DBs, stop DBs  
**RECOVERY:** Forensics required, full audit, CEO+CISO approval, gradual restoration

---

## 6. Post-Incident Review

### 6.1 Post-Incident Report Template

**SUMMARY:** ID, dates, duration, severity, category  
**IMPACT:** Users, financial loss, data compromised, downtime, regulatory  
**TIMELINE:** Chronological events  
**ROOT CAUSE:** Technical explanation  
**FACTORS:** Contributing factors  
**EVALUATION:** What went well, what to improve  
**ACTIONS:** Table (action, owner, priority, date, status)  
**LESSONS:** Key takeaways  
**SIGNATURES:** IC, CISO, CTO

### 6.2 PIR Meeting (Within 5 Days)

**ATTENDEES:** IC, responders, CISO, CTO, service owners, compliance  
**AGENDA (90min):** Timeline, RCA, response eval, action items, comms review, lessons

**BLAMELESS:** Focus on systems, not individuals

### 6.3 Metrics

**TRACK:** MTTD, MTTR, MTTC, MTTR, RTO  
**QUARTERLY:** Incidents by severity, averages, categories, completion rates, trends  
**ANNUAL:** Board report, benchmarks, budget recommendations

---

## 7. Training & Drills

### 7.1 Training

**NEW HIRE:** Overview (1h) - process, roles, reporting, what NOT to do  
**QUARTERLY (All Eng):** Runbook updates, case study, Q&A (30min)  
**ANNUAL CERT (Security/Admin/On-call):** Comprehensive (4h), hands-on, cert test

### 7.2 Tabletop Exercises (Quarterly)

**FORMAT:** Scenario discussion, no system changes  
**SCENARIOS:** Q1 wallet compromise, Q2 ransomware, Q3 DB breach, Q4 insider  
**PARTICIPANTS:** CISO, CTO, Security, DevOps, Compliance, Legal, PR (90min)

### 7.3 Full-Scale Drills (Annually)

**FORMAT:** Red team exercise, responders unaware  
**EVALUATION:** Detection, response, communication, containment, recovery  
**POST:** After-action report, feedback, runbook updates

---

## 8. Regulatory & Legal Considerations

### 8.1 When to Notify Regulators

| Incident Type | Regulator | Timing | Requirement |
|---------------|-----------|--------|-------------|
| Data Breach (PII) | State AGs | "Without unreasonable delay" | Mandatory if >[threshold] affected |
| Data Breach (EU) | GDPR Authority | 72 hours | Mandatory if "risk to rights" |
| Financial Loss >$5k | FinCEN SAR | 30 days | Mandatory (suspicious activity) |
| Customer Funds Lost | State transmitters | Immediate | Varies by state |
| Cyber Intrusion | FBI/Secret Service | Voluntary | Recommended |

### 8.2 When to Involve Law Enforcement

**RECOMMENDED:** Theft, ID theft breach, ransomware, insider threat, organized fraud  
**PROCEDURE:** Contact FBI Cyber/IC3, provide summary/evidence/impact, cooperate, coordinate

### 8.3 When to Involve Legal Counsel

**MANDATORY:** Regulatory notification, PII breach, insider threat, funds lost, media attention  
**PRIVILEGE:** Comms with counsel privileged, label "Attorney-Client Privileged", engage early

---

## Appendix A: Incident Response Tools

**MONITORING:** Datadog, CloudWatch, ELK, Wazuh/OSSEC, Suricata/Snort  
**FORENSICS:** Wireshark, Volatility, Autopsy, Chainalysis/Elliptic/TRM  
**COMMS:** Slack, PagerDuty, Zoom, StatusPage.io  
**ORCHESTRATION:** Ansible/Terraform, AWS CLI, custom scripts

---

## Appendix B: Quick Checklist

**P0 IMMEDIATE (15min):** Declare, war room, assess, notify CISO/CTO, contain, preserve evidence  
**NEXT 1H:** RCA initial, verify containment, escalate (CEO/Legal/Board), notify partners, external comms  
**NEXT 4H:** Eradicate, plan recovery, prep customer comms, file regulatory  
**NEXT 24H:** Execute recovery, monitor, initial report, schedule PIR  
**WITHIN 5 DAYS:** PIR meeting, post-mortem, action items, update runbook

---

## Appendix C: Emergency Contact Card

```
┌──────────────────────────────────────────────┐
│  ANIMICA CEX - EMERGENCY RESPONSE            │
├──────────────────────────────────────────────┤
│  SECURITY HOTLINE: +1-XXX-XXX-XXXX (24/7)   │
│  PAGERDUTY: incidents@animica.pagerduty.com  │
│  SLACK: #security-incidents                  │
├──────────────────────────────────────────────┤
│  CISO: +1-XXX-XXX-XXXX                       │
│  CTO: +1-XXX-XXX-XXXX                        │
│  BitGo: +1-XXX-XXX-XXXX                      │
├──────────────────────────────────────────────┤
│  QUICK RESPONSE:                             │
│  1. Declare (Slack #security-incidents)      │
│  2. Call Security Hotline                    │
│  3. DO NOT shut down compromised systems     │
│  4. Preserve evidence (logs, snapshots)      │
│  5. Follow /docs/incident_runbook.md         │
└──────────────────────────────────────────────┘
```

---

**Classification:** Internal - Security Sensitive  
**Distribution:** Security Team, On-call Engineers, Executive Leadership  
**Review Frequency:** Quarterly  
**Next Review:** April 2025  
**Approval:** CISO, CTO
