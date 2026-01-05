# Animica Compute Platform - Web UI Visual Guide

## 🎨 User Interface Overview

This document provides a visual guide to the Animica Compute Platform Web UI implementation.

---

## 📱 Application Structure

```
┌─────────────────────────────────────────────────────────────────┐
│  🏠 Animica Compute Platform                        🔔 Logout   │ ← Top Bar
├───────────────┬─────────────────────────────────────────────────┤
│               │                                                 │
│   Sidebar     │              Main Content Area                  │
│   Navigation  │                                                 │
│               │                                                 │
│  Animica      │                                                 │
│  Compute      │                                                 │
│               │                                                 │
│  ┌─────────┐  │                                                 │
│  │  Acme   │  │                                                 │
│  │  Inc.   │  │                                                 │
│  │ 10,000  │  │                                                 │
│  │ credits │  │                                                 │
│  └─────────┘  │                                                 │
│               │                                                 │
│  📊 Dashboard │                                                 │
│  💬 Chat      │                                                 │
│  🛠️ Workspace │                                                 │
│  🤖 Models    │                                                 │
│  💳 Billing   │                                                 │
│  ⚙️ Settings  │                                                 │
│               │                                                 │
│  ┌─────────┐  │                                                 │
│  │  User   │  │                                                 │
│  │  Info   │  │                                                 │
│  └─────────┘  │                                                 │
│               │                                                 │
└───────────────┴─────────────────────────────────────────────────┘
```

---

## 🔐 Authentication Pages

### Login Page
```
┌───────────────────────────────────────────┐
│                                           │
│          Animica Compute                  │
│       Sign in to your account             │
│                                           │
│   ┌─────────────────────────────────┐    │
│   │  Email                          │    │
│   │  ┌─────────────────────────┐    │    │
│   │  │ you@example.com         │    │    │
│   │  └─────────────────────────┘    │    │
│   │                                 │    │
│   │  Password                       │    │
│   │  ┌─────────────────────────┐    │    │
│   │  │ ••••••••                │    │    │
│   │  └─────────────────────────┘    │    │
│   │                                 │    │
│   │  ┌─────────────────────────┐    │    │
│   │  │      Sign in            │    │    │
│   │  └─────────────────────────┘    │    │
│   └─────────────────────────────────┘    │
│                                           │
│   Don't have an account? Sign up         │
│                                           │
└───────────────────────────────────────────┘
```

---

## 📊 Dashboard Page

```
┌─────────────────────────────────────────────────────────────────┐
│  Welcome back, user!                                            │
│  Here's an overview of your account and usage                   │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 💰 10,000│  │ 📁 3     │  │ 📊 127   │  │ 💬 8     │       │
│  │ Credits  │  │ Projects │  │ API Calls│  │ Sessions │       │
│  │ Remaining│  │ Active   │  │ (Today)  │  │          │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  Quick Actions                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ 💬 Start     │  │ 🛠️ Create    │  │ 🤖 View      │        │
│  │ Chat         │  │ Workspace    │  │ Models       │        │
│  │              │  │              │  │              │        │
│  │ Begin a new  │  │ Set up a new │  │ Explore      │        │
│  │ conversation │  │ coding       │  │ available    │        │
│  │ with AI      │  │ workspace    │  │ LLM models   │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│                                                                 │
│  Recent Activity                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 💬 Chat session completed                  2 hours ago  │   │
│  │ 🚀 Workspace deployed                      5 hours ago  │   │
│  │ 💳 Credits purchased                       1 day ago    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 💬 Chat Interface

```
┌─────────────────────────────────────────────────────────────────┐
│  ┌─────────────┐ │                                             │
│  │ + New Chat  │ │  ┌────────────────────────────────────┐     │
│  └─────────────┘ │  │ 👤 You                             │     │
│                  │  │ How do I implement authentication? │     │
│  Conversations   │  └────────────────────────────────────┘     │
│  ┌─────────────┐ │                                             │
│  │ Auth Setup  │ │  ┌────────────────────────────────────┐     │
│  │ 5 messages  │ │  │ 🤖 Assistant                       │     │
│  └─────────────┘ │  │ I can help you implement auth...   │     │
│  ┌─────────────┐ │  │ Here are the steps:                │     │
│  │ API Design  │ │  │ 1. Set up JWT tokens              │     │
│  │ 3 messages  │ │  │ 2. Create middleware              │     │
│  └─────────────┘ │  │ 3. Protected routes...            │     │
│                  │  └────────────────────────────────────┘     │
│                  │                                             │
│                  │  ┌────────────────────────────────────┐     │
│                  │  │ Type your message...              │     │
│                  │  │                                   │     │
│                  │  │                                   │     │
│                  │  │                    [Send]         │     │
│                  │  └────────────────────────────────────┘     │
│                  │  Model: llama-3-8b-instruct                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 💻 Code Workspace

```
┌─────────────────────────────────────────────────────────────────┐
│  ┌─────────────┐ │  src/index.ts          ●│ ┌──────────────┐ │
│  │ Project     │ │ ────────────────────────│ │ AI Assistant │ │
│  │             │ │                         │ │              │ │
│  │ 📄 index.ts │ │  1. import express      │ │ ┌──────────┐ │ │
│  │ 📄 auth.ts● │ │  2. const app = express()│ │ Generate  │ │ │
│  │ 📄 api.ts   │ │  3.                     │ │ Code      │ │ │
│  │             │ │  4. app.get('/', (req, │ │ └──────────┘ │ │
│  │ + New File  │ │  5.   res) => {         │ │              │ │
│  │ 🔗 GitHub   │ │  6.   res.send('Hello')│ │ Suggestions: │ │
│  └─────────────┘ │  7. })                  │ │              │ │
│                  │  8.                     │ │ • Fix syntax │ │
│                  │  9. app.listen(3000)   │ │ • Add docs   │ │
│                  │ 10.                     │ │ • Optimize   │ │
│                  │ ────────────────────────│ └──────────────┘ │
│                  │  Terminal           ▶ Run│                  │
│                  │ ────────────────────────│                  │
│                  │ $ npm run build         │                  │
│                  │ Compilation successful! │                  │
│                  │ $                       │                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🤖 Models Page

```
┌─────────────────────────────────────────────────────────────────┐
│  Available Models                                               │
│  Choose from our selection of powerful LLM models               │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Llama 3 8B   │  │ GPT-4        │  │ Claude 3 Opus│         │
│  │ Meta         │  │ OpenAI       │  │ Anthropic    │         │
│  │              │  │              │  │              │         │
│  │ Fast and     │  │ Most capable │  │ Excellent for│         │
│  │ efficient    │  │ model for    │  │ analysis and │         │
│  │              │  │ complex      │  │ creative     │         │
│  │              │  │ reasoning    │  │ tasks        │         │
│  │              │  │              │  │              │         │
│  │ Max: 8,192   │  │ Max: 8,192   │  │ Max: 4,096   │         │
│  │ Cost: $0.0001│  │ Cost: $0.03  │  │ Cost: $0.015 │         │
│  │              │  │              │  │              │         │
│  │ [Use Model]  │  │ [Use Model]  │  │ [Use Model]  │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 💳 Billing Page

```
┌─────────────────────────────────────────────────────────────────┐
│  Billing & Usage                                                │
│  Manage your subscription and payment methods                   │
│                                                                 │
│  Current Plan: Pro        10,000 credits remaining              │
│                                               [Upgrade Plan]    │
│                                                                 │
│  Usage This Month                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │ 📡 API   │  │ ⚡ GPU    │  │ 💾 Storage│                    │
│  │ 1,247/   │  │ 42/100   │  │ 3.2/10 GB │                    │
│  │ 10,000   │  │ hours    │  │           │                    │
│  │ ████░░░░ │  │ ████░░░░ │  │ ███░░░░░ │                    │
│  └──────────┘  └──────────┘  └──────────┘                     │
│                                                                 │
│  Available Plans                                                │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐               │
│  │ Starter    │  │ Pro ⭐     │  │ Enterprise │               │
│  │ $29/month  │  │ $99/month  │  │ $499/month │               │
│  │            │  │            │  │            │               │
│  │ 10K calls  │  │ 50K calls  │  │ Unlimited  │               │
│  │ 10 GPU hrs │  │ 50 GPU hrs │  │ 200 GPU hrs│               │
│  │ 5 GB       │  │ 25 GB      │  │ 100 GB     │               │
│  │            │  │            │  │            │               │
│  │ [Select]   │  │ [Current]  │  │ [Select]   │               │
│  └────────────┘  └────────────┘  └────────────┘               │
│                                                                 │
│  Payment Methods                                                │
│  🪙 ANM Token - Connected wallet                    [Default]   │
│  💳 Credit Card - •••• 4242                                     │
│  [+ Add Payment Method]                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Settings Page

```
┌─────────────────────────────────────────────────────────────────┐
│  Settings                                                       │
│                                                                 │
│  Profile | Organization | API Keys | Security                  │
│  ════════                                                       │
│                                                                 │
│  Profile Information                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Email:           user@example.com                      │   │
│  │  Wallet Address:  anm1qx2y3z...                        │   │
│  │  Role:            Owner                                 │   │
│  │                                                         │   │
│  │  [Update Profile]                                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Preferences                                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ☑ Email notifications                                  │   │
│  │  ☑ Usage alerts                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 👨‍💼 Admin Dashboard

```
┌─────────────────────────────────────────────────────────────────┐
│  Admin Dashboard                                                │
│  System administration and monitoring                           │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 👥 1,247 │  │ 🔥 89    │  │ 📡 125K  │  │ ⚡ 87%   │       │
│  │ Total    │  │ Active   │  │ API      │  │ GPU Usage│       │
│  │ Users    │  │ Sessions │  │ Requests │  │          │       │
│  │ +12%     │  │ +5%      │  │ +23%     │  │ +8%      │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  Recent Activity                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ ● user@example.com - Created workspace      2 min ago  │   │
│  │ ● dev@example.com - API rate limit           5 min ago  │   │
│  │ ● admin@example.com - Updated model         15 min ago  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Service Health          Resource Usage                         │
│  ┌──────────────────┐   ┌──────────────────┐                  │
│  │ ● API Gateway    │   │ CPU:    ████░░░░ │                  │
│  │ ● Inference Svc  │   │ Memory: ████████ │                  │
│  │ ● Auth Service   │   │ Disk:   ████░░░░ │                  │
│  │ ● Database       │   │ Network:███░░░░░ │                  │
│  └──────────────────┘   └──────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🎨 Design System

### Color Palette
```
Background:      Primary:         Success:
████ Slate 950   ████ Blue 600    ████ Green 500
████ Slate 900   ████ Blue 500    ████ Green 400
████ Slate 800   ████ Blue 400    

Text:            Warning:         Error:
████ White       ████ Yellow 500  ████ Red 500
████ Slate 300   ████ Yellow 400  ████ Red 400
████ Slate 500
```

### Typography
- **Headings**: 24px - 48px, Bold
- **Body**: 14px - 16px, Normal
- **Small**: 12px - 14px, Medium
- **Font**: Inter, system-ui

### Components
- **Cards**: Rounded corners, slate background
- **Buttons**: Primary (blue), Secondary (slate)
- **Inputs**: Dark with focus rings
- **Tables**: Striped rows, hover effects

---

## 📱 Responsive Design

The UI is optimized for desktop (1920x1080) with foundation for responsive:

- **Desktop**: Full sidebar + main content
- **Tablet**: Collapsible sidebar (future)
- **Mobile**: Bottom navigation (future)

---

## 🎯 Component Hierarchy

```
App
├── Router
│   ├── Public Routes
│   │   ├── LoginPage
│   │   └── RegisterPage
│   └── Protected Routes
│       └── Layout
│           ├── Sidebar
│           ├── TopBar
│           └── Outlet
│               ├── DashboardPage
│               ├── ChatPage
│               ├── WorkspacePage
│               ├── ModelsPage
│               ├── BillingPage
│               ├── SettingsPage
│               └── AdminPage
└── QueryClientProvider
```

---

## 🔄 Data Flow

```
User Action
    ↓
Component
    ↓
Store (Zustand)
    ↓
API Client (Axios)
    ↓
Backend Service
    ↓
Response
    ↓
Store Update
    ↓
Re-render
```

---

## ✨ Interactions

### Navigation
- Click sidebar items → Navigate to page
- Logo → Return to dashboard
- Logout → Clear session, redirect to login

### Chat
- Type message → Press Enter or Send
- Stream response → Real-time update
- Select model → Change inference engine

### Workspace
- Select file → Load in editor
- Edit code → Mark as modified
- Run code → Execute in sandbox

### Forms
- Fill inputs → Validate on submit
- Error → Show inline message
- Success → Redirect or show toast

---

## 📐 Layout Measurements

```
Sidebar:        256px wide
Top Bar:        64px tall
Content:        Remaining space
Card Padding:   24px
Button Height:  40px
Input Height:   40px
Border Radius:  8px
```

---

This visual guide demonstrates the complete user interface implementation for the Animica Compute Platform.
