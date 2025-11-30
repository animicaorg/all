/// Marketplace Implementation - Quick Reference & Testing Checklist
///
/// Use this document to:
/// 1. Verify all components are in place
/// 2. Test the implementation
/// 3. Track what needs to be done next

# ✅ Marketplace Implementation Checklist

## 📦 Code Delivery Status

### Services (lib/services/)
- [x] pricing_engine.dart
  - Lines: 400
  - Status: Complete & tested
  - Key: Deterministic pricing with treasury multiplier curve
  - Dependencies: None (pure Dart)
  
- [x] market_data_service.dart
  - Lines: 350
  - Status: Complete with fallback logic
  - Key: Multi-source price aggregation (CoinGecko → CoinMarketCap → Explorer)
  - Dependencies: http package
  
- [x] payment_gateway.dart
  - Lines: 550
  - Status: Stripe & PayPal fully coded, Apple Pay/Google Pay stubbed
  - Key: Extensible payment processing with 6 methods
  - Dependencies: http package (no external SDK dependencies required yet)

### State Management (lib/state/providers.dart)
- [x] Extended with marketplace providers (+400 lines)
  - Market data config, pricing engine, treasury snapshot
  - Purchase flow state machine (5 steps)
  - Purchase history & portfolio tracking
  - Dashboard summary aggregation
  - All providers have proper error handling via AsyncValue.when()

### UI Pages (lib/pages/marketplace/)
- [x] marketplace_home_page.dart (650 lines)
  - Hero price card with 24h change
  - Quick action buttons
  - Portfolio overview
  - Treasury progress
  - Market insights
  
- [x] buy_anm_page.dart (600 lines)
  - 5-step stepper (input → method → review → processing → receipt)
  - Real-time price calculation
  - Cost breakdown with fees
  - Status badges and completion flow
  
- [x] treasury_dashboard_page.dart (500 lines)
  - Revenue progress to $1B (hero card)
  - Key metrics grid (price, % sold, years to target, supply remaining)
  - Supply allocation pie chart
  - Price history line chart (7 days)
  - End-of-year simulation
  - Sales velocity table
  
- [x] purchase_history_page.dart (450 lines)
  - Summary cards (total ANM, spent, avg price, count)
  - Transaction list with status badges
  - Detail modals with full info
  - Export stubs (PDF/CSV ready)

### Shared Widgets (lib/widgets/chart_widget.dart)
- [x] LoadingOverlay
- [x] EmptyState
- [x] CurrencyInputField
- [x] PaymentMethodSelector
- [x] StatsRow / StatsCard
- [x] BarChart
- [x] InfoBanner

### Router (lib/router/marketplace_routes.dart)
- [x] All marketplace routes defined
- [x] Navigation helpers extension
- [x] Slide transitions configured
- [x] AnalyticsPage placeholder created

### Router Integration (lib/router.dart)
- [x] Imported marketplace_routes.dart
- [x] Added routes to ShellRoute

---

## 🧪 Manual Testing Steps

### Test 1: Basic Navigation
```
1. Launch app: flutter run -t lib/main.dart
2. Navigate to home page (default)
3. Tap "Buy ANM Now" button
4. Verify: Navigated to /marketplace/buy page
5. Tap back, verify returned to home
```

### Test 2: Purchase Flow (Steps 1-2)
```
1. Go to /marketplace/buy
2. Enter quantity: 1000
3. Verify: Price updates correctly
   - Current price displays (mocked or from API)
   - Cost breakdown shows subtotal + fees
4. Select payment method (any of 6)
5. Verify: Method highlighted, proceed to next step
```

### Test 3: Treasury Dashboard
```
1. From home, tap "Treasury Dashboard" quick action
2. Verify: Navigated to /marketplace/treasury
3. Verify: Progress card shows revenue/$1B target
4. Verify: Metrics grid displays (price, % sold, years, supply)
5. Verify: Charts render without errors (placeholder or real data)
6. Pull-down refresh: Data refreshes
```

### Test 4: Purchase History
```
1. From home, tap "History" quick action
2. Verify: Navigated to /marketplace/history
3. If no purchases: Empty state displays
4. Verify: Summary cards show (if purchases exist):
   - Total ANM
   - Total Spent
   - Avg Price
   - Purchase Count
```

### Test 5: Analytics Page
```
1. From home, tap "Analytics" quick action
2. Verify: Navigated to /marketplace/analytics
3. Verify: Placeholder charts display (custom paint or placeholder)
4. Verify: All sections render:
   - Pricing Curve Projection
   - Purchase Volume Trends
   - Market Concentration
```

### Test 6: Real-time Price Updates
```
1. Go to marketplace home
2. Observe price ticker
3. Verify: Updates occur (every 30s with current polling, or via WebSocket)
4. Verify: "Live indicator" shows data source
```

### Test 7: Pricing Logic
```
1. Open marketplace home or buy page
2. Verify: Default price is $1.00+ (based on market data)
3. Verify: Price calculation: max($1.00, marketPrice * 1.15) * treasuryMultiplier
4. Example:
   - Market price: $0.80
   - Effective: max($1.00, $0.92) * multiplier = $1.00 * multiplier
   - If 50% sold: multiplier = 1.0 + 2.0 * sqrt(0.5) = 1.0 + 1.414 = 2.414
   - Final: $1.00 * 2.414 = $2.414 per token
```

---

## 🔌 Integration Testing (Requires Backend)

### Test 1: RPC Method - explorer_getTreasurySnapshot
```
Expected RPC Response:
{
  "totalSupply": 1000000000.0,
  "soldToDate": 345000000.0,
  "treasuryBalance": 655000000.0,
  "percentSold": 34.5,
  "revenueToDate": 450000000.0,
  "lastUpdateBlock": 12345678,
  "timestamp": 1732462500000
}

Verify in App:
1. Treasury dashboard shows correct % sold
2. Remaining supply calculated correctly
3. Years to target calculation matches formula
```

### Test 2: RPC Method - explorer_getMarketData
```
Expected RPC Response:
{
  "price": 1.50,
  "marketCap": 1500000000.0,
  "volume24h": 45000000.0,
  "change24h": 12.5,
  "change7d": 35.2,
  "high24h": 1.55,
  "low24h": 1.40,
  "lastUpdate": 1732462500000,
  "source": "coingecko"
}

Verify in App:
1. Home page displays current price ($1.50 in example)
2. 24h change badge shows +12.5% (green)
3. Market cap and volume display correctly
```

### Test 3: RPC Method - wallet_getPurchaseHistory
```
Expected RPC Response:
{
  "purchases": [
    {
      "id": "tx_123",
      "timestamp": 1732462500000,
      "anmQuantity": 1000.0,
      "usdAmount": 1500.0,
      "pricePerAnm": 1.50,
      "paymentMethod": "credit_card",
      "status": "completed",
      "receiptUrl": "https://...",
      "transactionHash": "0x...",
      "fee": 45.0,
      "feePercentage": 3.0
    }
  ],
  "totalPurchases": 1,
  "totalAnmPurchased": 1000.0,
  "totalSpent": 1545.0,
  "averagePrice": 1.545
}

Verify in App:
1. Purchase history page displays transactions
2. Summary cards show correct totals
3. Each transaction shows correct details
```

---

## 🐛 Debugging Tips

### Issue: App crashes on launch
**Check**:
1. Run `flutter pub get` to ensure all dependencies installed
2. Run `dart analyze lib/` to check for syntax errors
3. Check console for import errors (missing files)
4. Verify Flutter version: `flutter --version`

### Issue: Pages don't render
**Check**:
1. Verify all widget classes exist in `lib/widgets/chart_widget.dart`
2. Check imports in page files
3. Run `flutter pub run build_runner build` if using code generation
4. Clear cache: `flutter clean && flutter pub get`

### Issue: Navigation doesn't work
**Check**:
1. Verify routes are in `lib/router/marketplace_routes.dart`
2. Verify routes are added to ShellRoute in `lib/router.dart`
3. Check route names match in extension methods
4. Enable GoRouter debugging: Add `debugLogDiagnostics: true` in GoRouter

### Issue: Price shows $0.00
**Check**:
1. Verify market data service can fetch from CoinGecko (no auth required for free tier)
2. Check network connectivity on device
3. Look for errors in console output
4. Verify provider is being watched correctly in UI

### Issue: Payment gateway errors
**Check**:
1. Verify environment variables are set (STRIPE_*, PAYPAL_*)
2. Verify API keys are correct (test keys for dev, live keys for prod)
3. Check Stripe/PayPal account status
4. Verify webhook endpoint is reachable (if testing webhooks)

---

## 📊 Performance Benchmarks

### Expected Performance Targets

| Operation | Target | Notes |
|-----------|--------|-------|
| Home page initial load | < 2s | Includes market data fetch |
| Buy page step change | < 100ms | UI-only state transition |
| Purchase history scroll | 60fps | With 100+ items |
| Price update | < 500ms | Includes API call |
| Chart rendering | < 1s | CustomPaint operations |

### Profiling Commands
```bash
# Launch with profiling
flutter run --profile

# Record timeline (Android)
flutter run --profile --trace-startup

# Memory profiling
dart devtools
```

---

## 📝 Code Quality Checks

### Pre-Commit Checklist
```bash
# Format code
dart format lib/ test/

# Analyze
dart analyze lib/

# Run tests
flutter test lib/

# Check imports
dart pub outdated
```

### Code Coverage
```bash
# Generate coverage
flutter test --coverage

# View coverage report
# Check coverage/ directory
```

---

## 🚀 Deployment Readiness Checklist

### Pre-Release
- [ ] All tests passing
- [ ] No console errors or warnings
- [ ] Performance targets met
- [ ] RPC endpoints configured (mainnet/testnet)
- [ ] Payment gateway keys set (test or live)
- [ ] Analytics configured
- [ ] Crash reporting enabled (Sentry/Crashlytics)
- [ ] Screenshots ready for app store
- [ ] Terms & Conditions updated
- [ ] Privacy Policy updated

### Release Build
```bash
# Android
flutter build apk --release
flutter build appbundle --release

# iOS
flutter build ios --release

# Web (if applicable)
flutter build web --release
```

### Post-Release Monitoring
- [ ] Monitor RPC call latency
- [ ] Monitor payment success rates
- [ ] Monitor crash reports
- [ ] Monitor user feedback (app store reviews)
- [ ] Monitor treasury balance changes
- [ ] Monitor purchase velocity

---

## 📞 Common Questions

### Q: How do I test payment flows without real payment gateway?
**A**: 
1. Use Stripe test cards (4242 4242 4242 4242)
2. Use PayPal sandbox accounts
3. Mock payment responses in tests
4. Use Stripe/PayPal dashboard to test webhooks

### Q: Can I test with real money for initial testing?
**A**: 
1. Use very small amounts ($1 USD recommended)
2. Test with 1 token instead of 1000
3. Use production payment gateway keys
4. Ensure RPC endpoint is correct (mainnet for real, testnet for testing)
5. Have refund process ready

### Q: What if market data API is down?
**A**: 
1. App automatically falls back to CoinGecko
2. If all APIs down, uses last cached price (5 min TTL)
3. If no cache, uses $1.00 base price as fallback
4. User is informed of data staleness

### Q: How do I add a new payment method?
**A**: 
1. Create new class extending PaymentGateway in `payment_gateway.dart`
2. Implement required methods
3. Add to PaymentProcessor registry
4. Add to PaymentMethodSelector UI
5. Test with sandbox/test keys

### Q: How do I update pricing formula?
**A**: 
1. Modify `PricingEngine.getCurrentPrice()` formula in `pricing_engine.dart`
2. Verify determinism (no randomness, no I/O)
3. Update unit tests with new formula
4. Update documentation with formula explanation
5. Deploy to all app versions simultaneously (critical for determinism)

---

## 🔗 Related Files

- **Pricing Logic**: `lib/services/pricing_engine.dart`
- **Market Data**: `lib/services/market_data_service.dart`
- **Payments**: `lib/services/payment_gateway.dart`
- **State Management**: `lib/state/providers.dart`
- **Navigation**: `lib/router.dart` + `lib/router/marketplace_routes.dart`
- **Integration Guide**: `MARKETPLACE_INTEGRATION_GUIDE.md`
- **This Checklist**: `MARKETPLACE_CHECKLIST.md`

---

**Document Status**: Ready for integration & testing
**Last Updated**: 2025-01-08
**Audience**: Developers, QA, DevOps, Product Team
