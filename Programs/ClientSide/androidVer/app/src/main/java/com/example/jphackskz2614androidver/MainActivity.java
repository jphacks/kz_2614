package com.example.jphackskz2614androidver;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;
import android.net.http.SslError;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.Locale;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "nen_gitsune_android";
    private static final String PREF_SERVER_URL = "server_url";
    private static final int REQUEST_RECORD_AUDIO = 3101;
    private static final int REQUEST_FILE_CHOOSER = 3102;

    private EditText addressField;
    private TextView hintView;
    private TextView statusView;
    private WebView webView;
    private Uri trustedServerUri;
    private PermissionRequest pendingAudioRequest;
    private ValueCallback<Uri[]> pendingFileChooser;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildScreen();
        configureWebView();

        SharedPreferences preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String savedUrl = preferences.getString(PREF_SERVER_URL, "");
        if (!savedUrl.isEmpty()) {
            addressField.setText(savedUrl);
            openServerPage();
        }
    }

    private void buildScreen() {
        int background = Color.rgb(250, 249, 246);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(background);

        TextView title = new TextView(this);
        title.setText("ねんぎつね");
        title.setTextColor(Color.rgb(65, 58, 49));
        title.setTextSize(20);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        title.setPadding(dp(18), dp(14), dp(18), dp(8));
        root.addView(title, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        LinearLayout addressRow = new LinearLayout(this);
        addressRow.setGravity(Gravity.CENTER_VERTICAL);
        addressRow.setPadding(dp(12), dp(4), dp(12), dp(8));
        addressField = new EditText(this);
        addressField.setSingleLine(true);
        addressField.setTextSize(14);
        addressField.setHint("https://サーバーのアドレス/");
        addressField.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        addressField.setImeOptions(EditorInfo.IME_ACTION_GO);
        addressRow.addView(addressField, new LinearLayout.LayoutParams(0, dp(48), 1f));

        Button openButton = new Button(this);
        openButton.setText("Webを開く");
        openButton.setOnClickListener(view -> openServerPage());
        addressRow.addView(openButton, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, dp(48)));
        root.addView(addressRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        addressField.setOnEditorActionListener((view, actionId, event) -> {
            boolean enter = event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER
                    && event.getAction() == KeyEvent.ACTION_DOWN;
            if (actionId == EditorInfo.IME_ACTION_GO || enter) {
                openServerPage();
                return true;
            }
            return false;
        });

        statusView = new TextView(this);
        statusView.setText("サーバーのURLを入力してWeb画面を開いてください。");
        statusView.setTextColor(Color.rgb(117, 111, 101));
        statusView.setTextSize(12);
        statusView.setPadding(dp(16), dp(2), dp(16), dp(8));
        root.addView(statusView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        hintView = new TextView(this);
        hintView.setText("音声会話を使うには、HTTPSで配信されたサーバー画面を開き、マイク利用を許可してください。\n\n例: https://192.168.1.10/");
        hintView.setTextColor(Color.rgb(111, 104, 94));
        hintView.setTextSize(15);
        hintView.setGravity(Gravity.CENTER);
        hintView.setPadding(dp(28), dp(28), dp(28), dp(28));
        root.addView(hintView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        webView = new WebView(this);
        webView.setVisibility(View.GONE);
        root.addView(webView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSafeBrowsingEnabled(true);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                return handleTopLevelNavigation(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleTopLevelNavigation(Uri.parse(url));
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                Uri page = Uri.parse(url);
                if (!sameOrigin(page, trustedServerUri)) {
                    view.stopLoading();
                    showStatus("許可したサーバー以外への移動を止めました。");
                    return;
                }
                showStatus("Web画面を開いています。音声会話はHTTPS接続で利用できます。");
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (sameOrigin(Uri.parse(url), trustedServerUri)) {
                    hintView.setVisibility(View.GONE);
                    webView.setVisibility(View.VISIBLE);
                    showStatus("接続しました。会話開始時にマイク利用を許可してください。");
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request.isForMainFrame()) {
                    showStatus("Webページを読み込めません。URLとサーバーの接続を確認してください。");
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                showStatus("HTTPS証明書を確認できないため接続を止めました。");
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(
                    WebView view,
                    ValueCallback<Uri[]> filePathCallback,
                    FileChooserParams fileChooserParams) {
                if (pendingFileChooser != null) {
                    pendingFileChooser.onReceiveValue(null);
                    pendingFileChooser = null;
                }
                if (!sameOrigin(Uri.parse(view.getUrl()), trustedServerUri)
                        || (fileChooserParams.getMode() != FileChooserParams.MODE_OPEN
                        && fileChooserParams.getMode() != FileChooserParams.MODE_OPEN_MULTIPLE)) {
                    filePathCallback.onReceiveValue(null);
                    return true;
                }

                pendingFileChooser = filePathCallback;
                try {
                    startActivityForResult(fileChooserParams.createIntent(), REQUEST_FILE_CHOOSER);
                    return true;
                } catch (Exception exception) {
                    pendingFileChooser = null;
                    filePathCallback.onReceiveValue(null);
                    Toast.makeText(MainActivity.this, "ファイル選択を開けません。", Toast.LENGTH_SHORT).show();
                    return true;
                }
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> requestAudioPermission(request));
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                if (pendingAudioRequest == request) pendingAudioRequest = null;
            }
        });
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_FILE_CHOOSER) return;

        ValueCallback<Uri[]> callback = pendingFileChooser;
        pendingFileChooser = null;
        if (callback == null) return;
        callback.onReceiveValue(resultCode == RESULT_OK
                ? WebChromeClient.FileChooserParams.parseResult(resultCode, data)
                : null);
    }

    private boolean handleTopLevelNavigation(Uri destination) {
        if (sameOrigin(destination, trustedServerUri)) return false;
        if ("http".equalsIgnoreCase(destination.getScheme()) || "https".equalsIgnoreCase(destination.getScheme())) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, destination));
            } catch (Exception ignored) {
                Toast.makeText(this, "このリンクを開けません。", Toast.LENGTH_SHORT).show();
            }
        }
        return true;
    }

    private void openServerPage() {
        String value = addressField.getText().toString().trim();
        if (value.isEmpty()) {
            addressField.setError("サーバーのURLを入力してください");
            return;
        }
        if (!value.contains("://")) value = "https://" + value;

        Uri uri = Uri.parse(value).normalizeScheme();
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!"https".equals(scheme) || uri.getHost() == null || uri.getUserInfo() != null) {
            addressField.setError("音声会話と管理通信にはHTTPSのURLを入力してください");
            showStatus("HTTPS以外の接続は利用できません。");
            return;
        }
        if (uri.getPath() == null || uri.getPath().isEmpty()) {
            uri = uri.buildUpon().appendPath("").build();
        }

        trustedServerUri = uri;
        String normalized = uri.toString();
        addressField.setText(normalized);
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                .putString(PREF_SERVER_URL, normalized)
                .apply();
        hintView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        showStatus("サーバーへ接続しています…");
        webView.loadUrl(normalized);
    }

    private void requestAudioPermission(PermissionRequest request) {
        String[] requested = request.getResources();
        boolean audioOnly = requested.length == 1
                && PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(requested[0]);
        if (!audioOnly || !sameOrigin(request.getOrigin(), trustedServerUri)) {
            request.deny();
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            return;
        }
        if (pendingAudioRequest != null) pendingAudioRequest.deny();
        pendingAudioRequest = request;
        requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_RECORD_AUDIO);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_RECORD_AUDIO) return;
        PermissionRequest request = pendingAudioRequest;
        pendingAudioRequest = null;
        if (request == null) return;
        boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (granted && sameOrigin(request.getOrigin(), trustedServerUri)) {
            request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            showStatus("マイクを許可しました。音声会話を開始できます。");
        } else {
            request.deny();
            showStatus("マイクが許可されていません。Androidのアプリ設定から許可できます。");
        }
    }

    private boolean sameOrigin(Uri left, Uri right) {
        if (left == null || right == null) return false;
        return originKey(left).equals(originKey(right));
    }

    private String originKey(Uri uri) {
        if (uri == null || uri.getScheme() == null || uri.getHost() == null) return "";
        String scheme = uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost().toLowerCase(Locale.ROOT);
        int port = uri.getPort();
        boolean defaultPort = ("https".equals(scheme) && port == 443)
                || ("http".equals(scheme) && port == 80);
        return scheme + "://" + host + ((port < 0 || defaultPort) ? "" : ":" + port);
    }

    private boolean isHttps(Uri uri) {
        return uri != null && "https".equalsIgnoreCase(uri.getScheme());
    }

    private void showStatus(String message) {
        if (statusView != null) statusView.setText(message);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onDestroy() {
        if (pendingAudioRequest != null) {
            pendingAudioRequest.deny();
            pendingAudioRequest = null;
        }
        if (pendingFileChooser != null) {
            pendingFileChooser.onReceiveValue(null);
            pendingFileChooser = null;
        }
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        super.onDestroy();
    }
}
