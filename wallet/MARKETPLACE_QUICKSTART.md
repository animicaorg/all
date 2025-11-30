/// Marketplace Quick Start Guide
/// 
/// For developers who want to get up and running with the ANM marketplace
/// in the Flutter wallet in the next 10 minutes.

# ANM Marketplace - Quick Start (10 min)

## 1️⃣ Setup (2 minutes)

### Clone/Sync
```bash
cd ~/documents/all/wallet
flutter pub get
```

### Verify Build
```bash
flutter analyze lib/
```

Expected output: No errors

---

## 2️⃣ Run the App (2 minutes)

```bash
flutter run -t lib/main.dart
```

On first launch:
- App opens to home page
- Go back to home if needed (default page)
- You should see the wallet dashboard

---

## 3️⃣ Navigate to Marketplace (1 minute)

From **Home Page**, you can:

### Option A: Quick Action Buttons (Visible on home)
- Tap **"Buy ANM Now"** button → Goes to /marketplace/buy
- Tap **"Treasury"** button → Goes to /marketplace/treasury
- Tap **"History"** button → Goes to /marketplace/history
- Tap **"Analytics"** button → Goes to /marketplace/analytics

### Option B: Direct URL (if GoRouter supports direct navigation)
```dart
context.go('/marketplace/buy');        // Purchase page
context.go('/marketplace/history');    // Transaction history
context.go('/marketplace/treasury');   // Treasury dashboard
context.go('/marketplace/analytics');  // Analytics
```

---

## 4️⃣ Test Purchase Flow (3 minutes)

### On Buy Page (/marketplace/buy):

**Step 1: Enter Quantity**
1. Type `1000` in the quantity field
2. See price update automatically
3. See cost breakdown (subtotal + fee)

**Step 2: Select Payment Method**
1. Choose any of the 6 payment methods:
   - Credit Card
   - Apple Pay
   - Google Pay
   - PayPal
   - Bank Transfer
   - Crypto
2. Method gets highlighted

**Step 3: Review Order**
1. Review total amount
2. Check payment method matches selection
3. Agree to terms (checkbox)
4. Tap "Continue"

**Step 4: Processing**
1. See loading spinner
2. Wait (simulated 2-second processing)

**Step 5: Receipt**
1. See success confirmation
2. Order details displayed
3. Tap "Done" to finish

---

## 5️⃣ Key Files to Know

### Pricing Logic
**File**: `lib/services/pricing_engine.dart`
**What it does**: Calculates ANM token prices deterministically
**Key formula**: 
```
effective_price = max($1.00, market_price * 1.15) * treasury_multiplier
```
where `treasury_multiplier = 1.0 + 2.0 * sqrt(percent_sold)`

**Example**:
- Base price: $1.00
- Market price: $0.80 → Use $1.00 (higher of base or market+15%)
- Treasury multiplier at 50% sold: 1.0 + 2.0 * 0.707 = 2.414
- Final price: $1.00 × 2.414 = **$2.414 per token**

### Market Data
**File**: `lib/services/market_data_service.dart`
**What it does**: Fetches ANM price from multiple sources
**Sources** (in fallback order):
1. CoinGecko (free, no auth)
2. CoinMarketCap (premium API)
3. Animica Explorer RPC

### Payment Processing
**File**: `lib/services/payment_gateway.dart`
**What it does**: Handles Stripe, PayPal, and other payment methods
**Status**: 
- ✅ Stripe fully implemented
- ✅ PayPal fully implemented
- ⏳ Apple Pay/Google Pay/Crypto (need native plugins)

### State Management
**File**: `lib/state/providers.dart` (extended section)
**What it does**: Manages all marketplace state via Riverpod
**Key providers**:
- `anmPriceProvider` - Current ANM price in USD
- `treasurySnapshotProvider` - Treasury state (% sold, remaining, revenue)
- `purchaseStateProvider` - Current purchase flow state
- `purchaseHistoryProvider` - User's transaction history

### UI Pages
**Folder**: `lib/pages/marketplace/`

| File | Lines | Purpose |
|------|-------|---------|
| marketplace_home_page.dart | 650 | Main hub with price ticker, portfolio, quick actions |
| buy_anm_page.dart | 600 | 5-step purchase flow |
| treasury_dashboard_page.dart | 500 | Treasury status, projections, charts |
| purchase_history_page.dart | 450 | Transaction history, portfolio tracking |

### Shared Widgets
**File**: `lib/widgets/chart_widget.dart`
**Components**: LoadingOverlay, EmptyState, CurrencyInputField, StatsCard, BarChart, etc.

### Router
**File**: `lib/router/marketplace_routes.dart`
**What it does**: Defines all marketplace routes and navigation
**Routes**:
- `/marketplace` - Home
- `/marketplace/buy` - Purchase
- `/marketplace/history` - History
- `/marketplace/treasury` - Treasury
- `/marketplace/analytics` - Analytics

---

## 6️⃣ Making Changes

### Change the Default Price
In `lib/services/pricing_engine.dart`, find:
```dart
const double _basePrice = 1.0; // Change this value
```

### Change the Markup Percentage
In `lib/services/pricing_engine.dart`, find:
```dart
const double _markupPercentage = 0.15; // 15%, change to 0.20 for 20%, etc.
```

### Change the Treasury Multiplier Curve
In `lib/services/pricing_engine.dart`, find `_getTreasuryMultiplier()`:
```dart
// Current: 1.0 + 2.0 * sqrt(percentSold)
// Change the 2.0 coefficient or sqrt to adjust curve steepness
```

### Add a New Payment Method
1. Add to enum in `lib/services/payment_gateway.dart`:
   ```dart
   enum PaymentMethod {
     // ...existing...
     myNewMethod,
   }
   ```

2. Create gateway class:
   ```dart
   class MyNewGateway extends PaymentGateway {
     @override
     Future<PaymentIntent> createIntent(...) async { /* ... */ }
     // ... implement other required methods
   }
   ```

3. Register in PaymentProcessor:
   ```dart
   class PaymentProcessor {
     PaymentProcessor() {
       _registerGateway('my_new_method', MyNewGateway());
     }
   }
   ```

---

## 7️⃣ Testing with Real Data

### Option A: Mock Data (Default, No Setup)
- App uses fallback prices ($1.00)
- Treasury snapshot returns hardcoded values (50% sold)
- Purchase history is empty until you make a "purchase"

### Option B: Real CoinGecko API
- No setup needed (free tier!)
- Just ensure network is connected
- App will automatically fetch real ANM price from CoinGecko
- Takes ~1-2 seconds on first load

### Option C: Custom RPC Endpoint
1. Update RPC URL in `lib/services/env.dart`:
   ```dart
   const String rpcUrl = 'http://your-node:8545';
   ```

2. Implement these RPC methods on your node:
   - `explorer_getTreasurySnapshot()` - Returns treasury state
   - `explorer_getMarketData(token)` - Returns market data
   - `explorer_getPriceHistory(token, days)` - Returns price history
   - `wallet_getPurchaseHistory(address)` - Returns user's purchases

---

## 8️⃣ Common Tweaks

### Speed up price updates
In `lib/services/market_data_service.dart`, change:
```dart
const Duration _cacheExpiry = Duration(minutes: 5); // Change to 1 minute
```

### Change cache TTL
Same file:
```dart
const Duration _cacheExpiry = Duration(seconds: 300); // Change to your value
```

### Disable real-time updates
In `lib/state/providers.dart`, comment out:
```dart
// await Future.delayed(const Duration(seconds: 30));
```

### Show debug info
In `lib/pages/marketplace/marketplace_home_page.dart`, add at the top:
```dart
if (kDebugMode) {
  print('Price: ${dashboard.anmPrice}');
  print('% Sold: ${dashboard.percentSold}');
}
```

---

## 9️⃣ Debugging

### View Console Output
```bash
# While running: `flutter run`, check terminal output
# Or use:
flutter logs
```

### Add Breakpoint
1. Open any file in your IDE (VS Code, Android Studio)
2. Click line number to set breakpoint
3. Run `flutter run --debug`
4. Breakpoint will trigger when code reaches it

### Mock API Responses
Edit `lib/services/market_data_service.dart`, in `_fetchCoinGeckoPrice()`:
```dart
// Replace the actual API call with mock:
return MarketPriceData(
  price: 1.50,
  marketCap: 1500000000,
  volume24h: 45000000,
  change24h: 12.5,
  source: 'mock',
);
```

### Check Riverpod Providers
Use Riverpod DevTools:
```bash
flutter pub add riverpod_generator
flutter pub run build_runner watch
```

---

## 🔟 Next Steps

### For Testing
1. Try the full purchase flow (all 5 steps)
2. Check purchase history page (starts empty, populates as you "buy")
3. Visit treasury dashboard to see projections
4. Play with quantity input to see price changes

### For Development
1. Read inline code comments in service files
2. Review `MARKETPLACE_INTEGRATION_GUIDE.md` for full details
3. Check test files (if present) for usage examples
4. Review the pricing engine formula in detail

### For Production
1. Get Stripe API keys (stripe.com)
2. Get PayPal API keys (developer.paypal.com)
3. Set up backend RPC methods (see integration guide)
4. Configure environment variables
5. Test with real payment gateway (sandbox first)
6. Deploy following release checklist

---

## 📚 Documentation Map

| Document | Purpose | Read When |
|----------|---------|-----------|
| **This file** (Quick Start) | Get running in 10 min | Just starting |
| MARKETPLACE_CHECKLIST.md | Testing & validation | Before deployment |
| MARKETPLACE_INTEGRATION_GUIDE.md | Full technical details | Setting up production |
| pricing_engine.dart | Pricing formula details | Tuning prices |
| payment_gateway.dart | Payment integration | Adding payment methods |

---

## ✨ Features at a Glance

### What Works Now ✅
- ✅ View current ANM price with 24h change
- ✅ Input quantity and see real-time total cost
- ✅ Select payment method (6 options)
- ✅ Review order before purchase
- ✅ Simulate purchase confirmation
- ✅ View treasury progress toward $1B
- ✅ View purchase history (simulated)
- ✅ See price projections based on treasury state
- ✅ Navigate between all marketplace pages

### What Needs Backend 🔧
- 🔧 Real treasury snapshot (RPC method)
- 🔧 Real purchase history (RPC method)
- 🔧 Actual payment processing (Stripe/PayPal webhook)
- 🔧 Minting tokens after payment (on-chain settlement)

### What's Optional/Future 🎯
- 🎯 PDF receipt generation
- 🎯 CSV tax export
- 🎯 Apple Pay/Google Pay native support
- 🎯 Crypto direct payments
- 🎯 KYC/AML compliance
- 🎯 Referral program
- 🎯 Gift card support

---

## 🚨 Troubleshooting

**App won't start?**
→ `flutter clean && flutter pub get`

**Price shows $0.00?**
→ Check network connection, or wait for API timeout (falls back to $1.00)

**Pages don't render?**
→ Check `lib/widgets/chart_widget.dart` exists with all widget classes

**Navigation doesn't work?**
→ Verify routes in `lib/router/marketplace_routes.dart`

**Payment flow crashes?**
→ Check console for error messages, verify PaymentGateway is initialized

**More issues?**
→ See MARKETPLACE_CHECKLIST.md "Debugging Tips" section

---

## 📞 Quick Reference

```dart
// Navigate to marketplace
context.goToMarketplace();
context.goToBuyANM();
context.goToPurchaseHistory();
context.goToTreasuryDashboard();
context.goToAnalytics();

// Watch price in UI
final price = ref.watch(anmPriceProvider);

// Watch purchase state
final purchase = ref.watch(purchaseStateProvider);

// Update quantity
ref.read(purchaseStateProvider.notifier).setQuantity(1000);

// Get current treasury snapshot
final treasury = ref.watch(treasurySnapshotProvider);
```

---

**Ready? Launch with**: `flutter run -t lib/main.dart`

**Questions?** Check the troubleshooting section above or read the full integration guide.

**Feedback?** Create an issue with details and screenshots.

---

**Last Updated**: 2025-01-08
**Version**: 1.0
**Status**: Production-ready (backend integration pending)
