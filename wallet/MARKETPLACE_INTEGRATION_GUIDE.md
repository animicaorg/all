/// Integration & Deployment Guide for Animica Flutter Wallet Marketplace
/// 
/// This document provides step-by-step instructions for integrating the
/// marketplace implementation with the existing wallet infrastructure.

# Marketplace Implementation - Complete Feature Set

## ✅ Completed Components

### 1. **Core Services** (`lib/services/`)
- ✓ `pricing_engine.dart` (400 lines)
  - Deterministic pricing with treasury multiplier curve
  - Formulas: `effectivePrice = max($1.00, exchangePrice * 1.15) * treasuryMultiplier`
  - Treasury multiplier: `1.0 + 2.0 * sqrt(percentSold)`
  - Methods: `getCurrentPrice()`, `getPriceAtPercentSold()`, `simulateEndOfYear()`, `priceToReachTarget`
  
- ✓ `market_data_service.dart` (350 lines)
  - Multi-source price aggregation (CoinGecko, CoinMarketCap, Explorer RPC)
  - Cache with 5-minute TTL
  - Fallback mechanism for reliability
  - Real-time WebSocket stream support
  - History fetching (7-30 days)

- ✓ `payment_gateway.dart` (550 lines)
  - Abstract `PaymentGateway` interface
  - `StripeGateway`: Payment intents, confirmation, refunds
  - `PayPalGateway`: OAuth flow, order creation/capture
  - `PaymentProcessor`: Registry and routing
  - 6 payment methods: Credit Card, Apple Pay, Google Pay, PayPal, Bank Transfer, Crypto
  - Deterministic fee calculation per method

### 2. **State Management** (`lib/state/providers.dart` - extended +400 lines)
- ✓ Market data providers
  - `marketDataConfigProvider`: Configuration singleton
  - `currentMarketPriceProvider`: FutureProvider<MarketPriceData>
  - `priceUpdatesStreamProvider`: StreamProvider for real-time updates
  
- ✓ Pricing providers
  - `treasurySnapshotProvider`: FutureProvider<TreasurySnapshot>
  - `pricingEngineProvider`: FutureProvider<PricingEngine>
  - `anmPriceProvider`: FutureProvider<double>
  - `priceAtPercentSoldProvider`: Family provider for projections
  - `eoySimulationProvider`: End-of-year projections
  
- ✓ Purchase flow
  - `PurchaseState` dataclass: Tracks quantity, method, intent, processing state
  - `PurchaseStateNotifier`: State machine with methods for each step
  - `purchaseStateProvider`: StateNotifierProvider<PurchaseStateNotifier, PurchaseState>
  
- ✓ History & portfolio
  - `purchaseHistoryProvider`: FutureProvider<List<HistoricalPurchase>>
  - `anmBalanceProvider`: FutureProvider<double> (sum of completed purchases)
  - `totalSpentProvider`: FutureProvider<double>
  - `averagePurchasePriceProvider`: FutureProvider<double>
  
- ✓ Dashboard aggregation
  - `DashboardSummary`: Combines all key metrics
  - `dashboardSummaryProvider`: Multi-future aggregation

### 3. **UI Pages** (`lib/pages/marketplace/`)
- ✓ `marketplace_home_page.dart` (650 lines)
  - Main hub with hero price card
  - Quick action buttons (history, treasury, analytics)
  - Portfolio overview card
  - Treasury progress card
  - Market insights grid
  
- ✓ `buy_anm_page.dart` (600 lines)
  - 5-step multi-step stepper UI
  - Step 1: Quantity input with real-time calculation
  - Step 2: Payment method selection (6 options)
  - Step 3: Order review with breakdown
  - Step 4: Processing indicator
  - Step 5: Receipt confirmation
  
- ✓ `treasury_dashboard_page.dart` (500 lines)
  - Revenue progress to $1B target (hero card)
  - Key metrics grid (price, % sold, years to target, remaining supply)
  - Supply allocation pie chart
  - Price history line chart (7 days)
  - End-of-year simulation card
  - Sales velocity table (7/30/90 day metrics)
  
- ✓ `purchase_history_page.dart` (450 lines)
  - Summary cards (total ANM, total spent, avg price, count)
  - Purchase list with status badges
  - Detail modals with full transaction info
  - Export stubs (PDF/CSV)

### 4. **Shared Widgets** (`lib/widgets/chart_widget.dart` - 400+ lines)
- ✓ `LoadingOverlay`: Full-screen loading indicator
- ✓ `EmptyState`: Placeholder for empty lists/states
- ✓ `CurrencyInputField`: Number input with USD formatting
- ✓ `PaymentMethodSelector`: Radio-like selection for payment methods
- ✓ `StatsRow` / `StatsCard`: Reusable stat cards with icons
- ✓ `BarChart`: Custom paint bar chart widget
- ✓ `InfoBanner`: Dismissible info/warning banners

### 5. **Router Configuration** (`lib/router/marketplace_routes.dart`)
- ✓ All marketplace routes defined
  - `/marketplace` → Main home page
  - `/marketplace/buy` → Purchase flow
  - `/marketplace/history` → Transaction history
  - `/marketplace/treasury` → Treasury dashboard
  - `/marketplace/analytics` → Analytics page (with placeholder implementation)
- ✓ Navigation helpers extension on BuildContext
  - `goToMarketplace()`, `goToBuyANM()`, `goToPurchaseHistory()`, `goToTreasuryDashboard()`, `goToAnalytics()`
- ✓ Slide transition animations between pages
- ✓ No transition for home (allows refresh)

### 6. **Router Integration** (`lib/router.dart` - updated)
- ✓ Imported `marketplace_routes.dart`
- ✓ Added `...marketplaceRoutes` to ShellRoute routes list
- ✓ All marketplace pages now accessible via GoRouter

---

## 🔧 Integration Checklist

### Phase 1: Validation & Environment Setup (PRE-DEPLOYMENT)

- [ ] **1. Install dependencies**
  ```bash
  flutter pub get
  ```
  
- [ ] **2. Generate code if using build_runner**
  ```bash
  flutter pub run build_runner build
  ```
  
- [ ] **3. Run static analysis**
  ```bash
  dart analyze lib/
  ```
  
- [ ] **4. Check compilation**
  ```bash
  flutter pub get && flutter compile kernel lib/main.dart
  ```

- [ ] **5. Configure environment variables** (create `.env` file or export)
  ```bash
  export STRIPE_PUBLISHABLE_KEY="pk_test_..."
  export STRIPE_SECRET_KEY="sk_test_..."
  export PAYPAL_CLIENT_ID="YOUR_CLIENT_ID"
  export PAYPAL_CLIENT_SECRET="YOUR_SECRET"
  export COINGECKO_API_KEY=""  # Optional, free tier has limits
  export COINMARKETCAP_API_KEY="YOUR_KEY"
  export ANIMICA_RPC_URL="http://localhost:8545"
  export ANIMICA_EXPLORER_URL="http://localhost:3000"
  ```

- [ ] **6. Verify asset files** (ensure placeholders exist)
  - Payment method icons (stripe, paypal, apple, google logos)
  - Chart placeholder images if needed
  
- [ ] **7. Create test fixtures** for integration testing
  - Treasury snapshot samples
  - Market data samples
  - Payment intent responses

### Phase 2: Backend RPC Methods (CRITICAL BLOCKERS)

The following RPC methods MUST be implemented on the Animica node for real data:

#### **2.1 Treasury Snapshot Method**
```
RPC Method: explorer_getTreasurySnapshot
Returns: {
  "totalSupply": 1000000000.0,         // Total ANM in existence
  "soldToDate": 345000000.0,          // ANM sold from treasury so far
  "treasuryBalance": 655000000.0,     // ANM remaining in treasury
  "percentSold": 34.5,                // (soldToDate / totalSupply) * 100
  "revenueToDate": 450000000.0,       // USD revenue generated to date
  "lastUpdateBlock": 12345678,        // Block height of last update
  "timestamp": 1732462500000          // Unix milliseconds
}
```

**Implementation Location**: Backend consensus/execution layer
**Data Source**: Chain state (treasury account balance + cumulative sales record)
**Update Frequency**: Every block (or cached, updated every N blocks)

#### **2.2 Market Data Method**
```
RPC Method: explorer_getMarketData(token)
Parameters: token = "ANM" or contract address
Returns: {
  "price": 1.50,                      // Current USD price
  "marketCap": 1500000000.0,          // Total market cap
  "volume24h": 45000000.0,            // 24-hour trading volume
  "change24h": 12.5,                  // 24-hour price change %
  "change7d": 35.2,                   // 7-day price change %
  "high24h": 1.55,                    // 24-hour high
  "low24h": 1.40,                     // 24-hour low
  "lastUpdate": 1732462500000,        // Unix milliseconds
  "source": "coingecko|coinmarketcap|exchange"
}
```

**Implementation Location**: Bridge to market data service (aggregates from external APIs)
**Data Source**: CoinGecko/CoinMarketCap APIs + internal exchange if available
**Update Frequency**: Every 30-60 seconds (can cache)
**Fallback**: Market data service has built-in fallback to CoinGecko

#### **2.3 Price History Method**
```
RPC Method: explorer_getPriceHistory(token, days)
Parameters: token = "ANM", days = 1|7|30|90
Returns: {
  "prices": [1.00, 1.01, 1.02, ..., 1.50],  // Historical prices
  "timestamps": [ts1, ts2, ts3, ..., tsN],   // Matching timestamps
  "period": "7d",
  "currency": "USD"
}
```

**Implementation Location**: Backend data service (maintains price history)
**Data Source**: CoinGecko historical API or internal archive
**Update Frequency**: Daily historical snapshots
**Cache**: 1-hour minimum (historical data is stable)

#### **2.4 Purchase History Method**
```
RPC Method: wallet_getPurchaseHistory(address)
Parameters: address = user's wallet address
Returns: {
  "purchases": [
    {
      "id": "tx_hash_or_order_id",
      "timestamp": 1732462500000,
      "anmQuantity": 1000.0,
      "usdAmount": 1500.0,
      "pricePerAnm": 1.50,
      "paymentMethod": "credit_card|paypal|crypto|bank",
      "status": "completed|pending|failed",
      "receiptUrl": "https://...",
      "transactionHash": "0x...",
      "fee": 45.0,
      "feePercentage": 3.0
    },
    // ... more purchases
  ],
  "totalPurchases": 5,
  "totalAnmPurchased": 5000.0,
  "totalSpent": 7500.0,
  "averagePrice": 1.50
}
```

**Implementation Location**: Backend wallet/tx history service
**Data Source**: On-chain payment settlement records + off-chain payment gateway records
**Update Frequency**: Real-time after payment confirmation
**Access Control**: User can only query their own history (signed with private key)

---

### Phase 3: Payment Gateway Setup

#### **3.1 Stripe Integration**
- [ ] Create Stripe account (stripe.com)
- [ ] Get Publishable Key (pk_test_*) and Secret Key (sk_test_*)
- [ ] Enable Payment Methods:
  - Credit/Debit Cards (enabled by default)
  - Bank Transfer (ACH for US, SEPA for EU)
  - Wallets (Apple Pay, Google Pay)
- [ ] Set up webhooks for payment confirmations
  - Endpoint: `POST /api/webhooks/stripe`
  - Events: `payment_intent.succeeded`, `payment_intent.payment_failed`
- [ ] Configure return URLs
  - Success: `myapp://marketplace/buy?status=success&session={session_id}`
  - Cancel: `myapp://marketplace/buy?status=cancel`
- [ ] Test with sandbox keys before going live

#### **3.2 PayPal Integration**
- [ ] Create PayPal Business account
- [ ] Get Client ID and Client Secret
- [ ] Enable Checkout (Orders API)
- [ ] Configure return URLs
  - Return: `myapp://marketplace/buy?status=success&orderId={ORDER_ID}`
  - Cancel: `myapp://marketplace/buy?status=cancel`
- [ ] Test with sandbox credentials

#### **3.3 Webhook Endpoint**
- [ ] Create backend endpoint to receive payment confirmations
  ```
  POST /api/webhooks/payment
  Payload: {
    "provider": "stripe|paypal",
    "orderId": "order_123",
    "status": "success|failed",
    "amount": 1500.0,
    "amountCurrency": "USD",
    "anmQuantity": 1000.0,
    "userAddress": "0x...",
    "transactionHash": "tx_...",
    "timestamp": 1732462500000
  }
  ```
- [ ] Verify webhook signatures (Stripe: HMAC-SHA256, PayPal: signature)
- [ ] Call on-chain treasury settlement contract
- [ ] Update purchase history DB
- [ ] Notify app via RPC method update or WebSocket

---

### Phase 4: On-Chain Settlement

#### **4.1 Treasury Contract Methods**
- [ ] Implement `mintToUser(address user, uint256 amount)` function
  - Mints ANM tokens to user after payment confirmation
  - Records transaction in treasury history
  - Emits `Transfer` and `MintFromTreasury` events
  - Only callable by treasurer/multisig
  
- [ ] Implement `recordSale(address buyer, uint256 anmAmount, uint256 pricePerToken, address paymentMethod)`
  - Records cumulative sale for pricing curve
  - Updates treasury balance
  - Tracks payment method for analytics
  
- [ ] Implement access control
  - Only authorized payment processor can call mint
  - Use multisig for large transactions (>$100k)

#### **4.2 State Updates**
- [ ] Update treasury snapshot after each mint
  - Decrement `treasuryBalance`
  - Increment `soldToDate`
  - Update `percentSold` and `revenueToDate`
- [ ] Emit events for indexing
  - `TreasuryMint(buyer, anmAmount, usdAmount, pricePerToken, timestamp)`
  - `TreasurySnapshot(totalSupply, soldToDate, treasuryBalance, percentSold, timestamp)`

---

### Phase 5: Testing & QA

#### **5.1 Unit Tests**
- [ ] Test pricing engine calculations
  ```bash
  cd lib/services && flutter test pricing_engine_test.dart
  ```
  - Verify `$1.00` base price
  - Verify 15% markup application
  - Verify treasury multiplier curve (sqrt-based)
  - Test boundary cases (0% sold, 100% sold)

- [ ] Test market data service
  ```bash
  flutter test lib/services/market_data_service_test.dart
  ```
  - Mock CoinGecko responses
  - Mock fallback to CoinMarketCap
  - Test cache TTL behavior
  - Test error handling

- [ ] Test payment gateway
  ```bash
  flutter test lib/services/payment_gateway_test.dart
  ```
  - Mock Stripe API
  - Mock PayPal API
  - Test fee calculations
  - Test payment intent creation

#### **5.2 Integration Tests**
- [ ] Test purchase flow end-to-end (with mocks)
  - Quantity input → Payment method selection → Review → Processing → Receipt
  - Verify state transitions
  - Verify correct final price calculation
  
- [ ] Test RPC method calls
  - Mock `explorer_getTreasurySnapshot` responses
  - Mock `wallet_getPurchaseHistory` responses
  - Verify providers correctly parse data
  
- [ ] Test navigation
  ```bash
  flutter test integration_test/marketplace_navigation_test.dart
  ```
  - Home → Buy → Success flow
  - Home → History
  - Home → Treasury Dashboard
  - Home → Analytics

#### **5.3 Manual Testing (Simulator/Device)**
- [ ] Launch on iOS simulator
  ```bash
  flutter run -d iOS -t lib/main.dart
  ```
  
- [ ] Launch on Android emulator
  ```bash
  flutter run -d Android -t lib/main.dart
  ```
  
- [ ] Manual test flow with test payment methods
  - Stripe test card: 4242 4242 4242 4242
  - PayPal sandbox account
  - Verify receipt generation
  
- [ ] Test error states
  - Invalid quantity (zero, negative, exceeds treasury)
  - Payment method unavailable
  - Network timeout
  - Insufficient funds

#### **5.4 Performance Testing**
- [ ] Test with large datasets (>10,000 purchase history items)
  - Pagination works smoothly
  - Purchase history page doesn't lag
  
- [ ] Test with slow network (3G)
  - Timeouts handled gracefully
  - Fallbacks work as expected
  
- [ ] Test memory usage
  - No memory leaks during repeated navigation
  - Cache cleanup works

---

### Phase 6: Deployment & Monitoring

#### **6.1 Build Release APK/IPA**
```bash
# Android release
flutter build apk --release

# iOS release
flutter build ios --release
```

#### **6.2 Configure Analytics**
- [ ] Firebase Analytics (if applicable)
  - Track purchase flow completion rates
  - Track cart abandonment
  - Track pricing tier distribution
  
- [ ] Error tracking (Sentry, Crashlytics)
  - Monitor payment gateway errors
  - Monitor RPC failures
  
#### **6.3 Monitoring & Alerts**
- [ ] Set up monitoring for:
  - `explorer_getTreasurySnapshot` RPC call latency
  - `explorer_getMarketData` freshness (max age)
  - Payment gateway success rates
  - Purchase flow completion rates
  
- [ ] Alert thresholds:
  - RPC latency > 2 seconds
  - Payment method unavailable
  - Price data stale > 5 minutes

#### **6.4 Deployment Checklist**
- [ ] All environment variables set in production
- [ ] Payment gateway keys are production keys (not test)
- [ ] RPC endpoint points to mainnet
- [ ] Backup/recovery procedure documented
- [ ] Rollback procedure documented
- [ ] Support runbook created
- [ ] Status page updates planned

---

## 📝 Configuration Files Reference

### `.env` / Environment Variables
```bash
# Stripe
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_SECRET_KEY=sk_live_...

# PayPal
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_MODE=live  # "sandbox" or "live"

# Market Data APIs
COINGECKO_API_KEY=  # Optional (free tier works)
COINMARKETCAP_API_KEY=...

# Blockchain
ANIMICA_RPC_URL=https://rpc.animica.io
ANIMICA_EXPLORER_URL=https://explorer.animica.io
ANIMICA_CHAIN_ID=1  # Mainnet

# App Config
APP_FLAVOR=production
DEBUG_MODE=false
CACHE_TTL_SECONDS=300
```

### `pubspec.yaml` (Already includes required dependencies)
```yaml
dependencies:
  flutter: sdk: flutter
  go_router: ^14.2.0
  riverpod: ^2.5.1
  http: ^1.2.1
  cbor: ^6.1.1
  # ... (other deps)
```

---

## 🚀 Running the App with Marketplace Enabled

### From Command Line
```bash
# Dev flavor (with dev tools)
flutter run -t lib/main.dart --flavor dev

# Production flavor (no dev tools)
flutter run -t lib/main.dart --flavor production

# With specific device
flutter run -d <device_id> -t lib/main.dart
```

### From IDE
- Set breakpoint on `marketplaceRoutes` in `router.dart`
- Run app via VS Code / Android Studio
- Navigate to home page, tap marketplace actions

### Accessing Marketplace Pages
- **Home**: `/` → tap "Buy ANM Now" or quick action buttons
- **Purchase**: `/marketplace/buy`
- **History**: `/marketplace/history`
- **Treasury**: `/marketplace/treasury`
- **Analytics**: `/marketplace/analytics`

---

## 📚 File Structure Reference

```
wallet/
├── lib/
│   ├── main.dart                                  # App entry point
│   ├── router.dart                               # GoRouter config (updated)
│   ├── router/
│   │   └── marketplace_routes.dart              # ✓ Marketplace routes + AnalyticsPage
│   ├── services/
│   │   ├── pricing_engine.dart                  # ✓ Pricing calculations
│   │   ├── market_data_service.dart            # ✓ Price feeds
│   │   ├── payment_gateway.dart                # ✓ Payment processing
│   │   └── env.dart                            # Environment config
│   ├── state/
│   │   └── providers.dart                      # ✓ Riverpod providers (extended)
│   ├── pages/
│   │   ├── marketplace/
│   │   │   ├── marketplace_home_page.dart      # ✓ Main hub
│   │   │   ├── buy_anm_page.dart              # ✓ Purchase flow
│   │   │   ├── treasury_dashboard_page.dart   # ✓ Treasury status
│   │   │   └── purchase_history_page.dart     # ✓ Transaction history
│   │   └── ...other pages...
│   ├── widgets/
│   │   ├── chart_widget.dart                  # ✓ Shared UI components
│   │   └── ...other widgets...
│   └── keyring/
│       └── keyring.dart                       # Wallet management
├── test/
│   ├── marketplace_pricing_test.dart          # TODO: Unit tests
│   ├── marketplace_integration_test.dart      # TODO: Integration tests
│   └── ...other tests...
└── integration_test/
    └── marketplace_flow_test.dart             # TODO: E2E tests
```

---

## ✨ Next Steps (Post-Integration)

### Short-term (Week 1-2)
1. [ ] Implement RPC methods on backend (treasury snapshot, market data, purchase history)
2. [ ] Set up Stripe/PayPal accounts and get credentials
3. [ ] Run unit tests to verify pricing logic
4. [ ] Test purchase flow on simulator with mocked data

### Medium-term (Week 3-4)
1. [ ] Implement payment webhook endpoint
2. [ ] Integrate on-chain treasury contract minting
3. [ ] Set up production payment gateway keys
4. [ ] Create marketing materials (screenshots, demo video)

### Long-term (Month 2+)
1. [ ] A/B test pricing strategies
2. [ ] Implement KYC/AML compliance layer
3. [ ] Add PDF receipt export
4. [ ] Launch analytics dashboard for ops team
5. [ ] Expand to additional payment methods (crypto direct, wire transfer, etc.)

---

## 💡 Troubleshooting

### Issue: "explorer_getTreasurySnapshot RPC method not found"
**Cause**: Backend hasn't implemented the RPC method yet
**Solution**: Implement RPC method on Animica node following spec above

### Issue: Payment gateway throws "API key not found"
**Cause**: Environment variables not set
**Solution**: Export all env vars from Phase 1, or set in `lib/services/env.dart`

### Issue: "UI widgets not found" compile error
**Cause**: chart_widget.dart import missing or LoadingOverlay not defined
**Solution**: Verify `lib/widgets/chart_widget.dart` exists with all widget classes

### Issue: Price displays $0.00 on home page
**Cause**: Market data provider returning null or error
**Solution**: Check network connection, API keys, RPC endpoint URL in console

### Issue: Purchase flow doesn't advance past step 2
**Cause**: Payment gateway not initialized or RPC calls failing
**Solution**: Check `paymentProcessorProvider` in providers.dart, verify API keys

---

## 📞 Support

For issues or questions:
1. Check the troubleshooting section above
2. Review inline code comments in service files
3. Check integration test examples for usage patterns
4. Refer to Flutter/Dart docs for framework-specific questions
5. Contact backend team for RPC method implementation

---

**Last Updated**: 2025-01-08
**Implementation Status**: ✓ Complete (all UI/service/state code done)
**Blockers**: ⏳ Backend RPC methods, payment gateway keys, webhook endpoint
**Ready for**: Testing with mocked data, code review, integration planning
