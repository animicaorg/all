/// Deep Linking Handler for Animica Apps
///
/// Enables navigation between wallet and explorer using the animica:// URI scheme.
/// 
/// Supported deep links:
/// - animica://marketplace/buy — Open wallet to buy page
/// - animica://marketplace/history — Open wallet to purchase history
/// - animica://marketplace/treasury — Open wallet treasury dashboard
/// - animica://tx/<hash> — Open wallet transaction details
/// - animica://address/<address> — Open wallet address view
/// - animica://explorer/<path> — Open explorer (web)
/// 
/// Usage in Explorer:
/// ```
/// window.open('animica://marketplace/buy');
/// window.location.href = 'animica://tx/0x123...';
/// ```
///
/// Usage in Wallet:
/// ```
/// context.handleDeepLink('animica://marketplace/buy');
/// GoRouter.of(context).handleDeepLink('animica://address/0x789...');
/// ```

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:uni_links/uni_links.dart';
import 'dart:async';

import '../router.dart';

/// Deep link handler for processing animica:// URIs
class DeepLinkHandler {
  final GoRouter router;
  StreamSubscription? _deepLinkSubscription;

  DeepLinkHandler({required this.router});

  /// Initialize deep link listener
  void init(BuildContext context) {
    _deepLinkSubscription = deepLinkStream.listen(
      (String deepLink) => _handleDeepLink(deepLink, context),
      onError: (err) {
        debugPrint('Error handling deep link: $err');
      },
    );
  }

  /// Handle a single deep link
  Future<void> _handleDeepLink(String deepLink, BuildContext context) async {
    debugPrint('Deep link received: $deepLink');
    
    try {
      final uri = Uri.parse(deepLink);
      
      if (uri.scheme != 'animica') {
        debugPrint('Unsupported URI scheme: ${uri.scheme}');
        return;
      }
      
      final host = uri.host;
      final pathSegments = uri.pathSegments;
      
      // Route based on host and path
      switch (host) {
        case 'marketplace':
          _handleMarketplaceLink(pathSegments, context);
          break;
        case 'tx':
          _handleTransactionLink(pathSegments, context);
          break;
        case 'address':
          _handleAddressLink(pathSegments, context);
          break;
        case 'explorer':
          _handleExplorerLink(pathSegments);
          break;
        default:
          debugPrint('Unknown deep link host: $host');
      }
    } catch (e) {
      debugPrint('Error parsing deep link: $e');
    }
  }

  /// Handle marketplace-related deep links
  void _handleMarketplaceLink(List<String> pathSegments, BuildContext context) {
    if (pathSegments.isEmpty) {
      // animica://marketplace → home
      router.go('/marketplace');
      return;
    }
    
    final action = pathSegments[0];
    switch (action) {
      case 'buy':
        // animica://marketplace/buy → buy page
        router.go('/marketplace/buy');
        break;
      case 'history':
        // animica://marketplace/history → purchase history
        router.go('/marketplace/history');
        break;
      case 'treasury':
        // animica://marketplace/treasury → treasury dashboard
        router.go('/marketplace/treasury');
        break;
      case 'analytics':
        // animica://marketplace/analytics → analytics
        router.go('/marketplace/analytics');
        break;
      default:
        debugPrint('Unknown marketplace action: $action');
    }
  }

  /// Handle transaction deep links
  void _handleTransactionLink(List<String> pathSegments, BuildContext context) {
    if (pathSegments.isEmpty) return;
    
    final txHash = pathSegments[0];
    // animica://tx/<hash> → transaction details
    // TODO: Navigate to transaction detail page
    debugPrint('Navigate to transaction: $txHash');
    
    // For now, navigate to home and pass tx hash via query params
    router.go(
      '/',
      extra: {'selectedTx': txHash},
    );
  }

  /// Handle address deep links
  void _handleAddressLink(List<String> pathSegments, BuildContext context) {
    if (pathSegments.isEmpty) return;
    
    final address = pathSegments[0];
    // animica://address/<address> → address view/receive
    // TODO: Navigate to address detail page
    debugPrint('Navigate to address: $address');
    
    router.go(
      '/receive',
      extra: {'address': address},
    );
  }

  /// Handle explorer links (opens web browser)
  void _handleExplorerLink(List<String> pathSegments) {
    final explorerUrl = 'https://explorer.animica.io';
    final path = pathSegments.isNotEmpty ? '/${pathSegments.join('/')}' : '';
    final url = '$explorerUrl$path';
    
    // animica://explorer/<path> → open in browser
    // TODO: Use url_launcher package
    debugPrint('Open explorer URL: $url');
    
    // launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
  }

  /// Create a deep link from wallet to explorer
  static String createExplorerLink(String path) {
    return 'https://explorer.animica.io$path';
  }

  /// Create a deep link to marketplace
  static String createMarketplaceLink(String action) {
    return 'animica://marketplace/$action';
  }

  /// Create a deep link to transaction
  static String createTransactionLink(String txHash) {
    return 'animica://tx/$txHash';
  }

  /// Create a deep link to address
  static String createAddressLink(String address) {
    return 'animica://address/$address';
  }

  /// Dispose resources
  void dispose() {
    _deepLinkSubscription?.cancel();
  }
}

/// Extension to easily navigate using deep links
extension DeepLinkNavigation on BuildContext {
  /// Navigate to explorer marketplace page
  void openExplorerMarketplace() {
    // In a Flutter web environment, use window.open
    // In native, use the animica:// scheme
    // For now, just navigate within wallet
    GoRouter.of(this).go('/marketplace');
  }

  /// Navigate to explorer (on web, opens in new tab)
  void openExplorer(String path) {
    // In Flutter web:
    // final url = DeepLinkHandler.createExplorerLink(path);
    // html.window.open(url, 'explorer');
    
    // In native, navigate to marketplace as fallback
    GoRouter.of(this).go('/marketplace');
  }

  /// Navigate to transaction (local or explorer)
  void openTransaction(String txHash, {bool inExplorer = false}) {
    if (inExplorer) {
      openExplorer('/tx/$txHash');
    } else {
      // TODO: Implement transaction detail page
      GoRouter.of(this).go('/', extra: {'selectedTx': txHash});
    }
  }

  /// Navigate to address (local or explorer)
  void openAddress(String address, {bool inExplorer = false}) {
    if (inExplorer) {
      openExplorer('/address/$address');
    } else {
      GoRouter.of(this).go('/receive', extra: {'address': address});
    }
  }
}

/// Mobile deep link configuration (Android/iOS)
class MobileDeepLinkConfig {
  /// Android deep link intent filters
  static const androidManifestConfig = '''
<activity>
  <intent-filter android:label="@string/app_name">
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    
    <!-- Marketplace links -->
    <data
      android:scheme="animica"
      android:host="marketplace"
      android:path="/buy"
      />
    <data
      android:scheme="animica"
      android:host="marketplace"
      android:path="/history"
      />
    <data
      android:scheme="animica"
      android:host="marketplace"
      android:path="/treasury"
      />
    
    <!-- Transaction links -->
    <data
      android:scheme="animica"
      android:host="tx"
      android:pathPrefix="/"
      />
    
    <!-- Address links -->
    <data
      android:scheme="animica"
      android:host="address"
      android:pathPrefix="/"
      />
  </intent-filter>
</activity>
''';

  /// iOS URL scheme configuration (Info.plist)
  static const iosUrlSchemeConfig = '''
<dict>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeRole</key>
      <string>Editor</string>
      <key>CFBundleURLName</key>
      <string>io.animica.wallet</string>
      <key>CFBundleURLSchemes</key>
      <array>
        <string>animica</string>
      </array>
    </dict>
  </array>
</dict>
''';
}

/// Web deep link handler (for Flutter web)
class WebDeepLinkHandler {
  /// Handle deep links on Flutter web
  static void initWeb() {
    // In Flutter web, use window.location.href to handle navigation
    // This is typically handled by the browser's routing
    
    // Check if there's a deep link in the URL hash
    // example: animica-wallet.web.app#/marketplace/buy
    // This is handled automatically by GoRouter
  }

  /// Open a link to another Animica app
  static void openApp(String scheme, String path) {
    // On web, we can use web links or deep links depending on context
    // For now, construct a URL
    final uri = Uri(scheme: scheme, host: '', path: path);
    // window.open(uri.toString(), '_blank');
  }

  /// Navigate to explorer
  static void openExplorer(String path) {
    // On web, simply navigate
    // window.location.href = 'https://explorer.animica.io$path';
  }
}
