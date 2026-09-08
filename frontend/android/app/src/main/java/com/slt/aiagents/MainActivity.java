package com.slt.aiagents;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;
import android.webkit.WebView;

public class MainActivity extends BridgeActivity {
    private static final String LOCAL_AUTH_CALLBACK = "http://localhost:3000/auth/callback";

    @Override
    protected void load() {
        super.load();

        bridge.setWebViewClient(new BridgeWebViewClient(bridge) {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);

                view.evaluateJavascript(
                    "(function(){"
                        + "var body=document.body&&document.body.innerText||'';"
                        + "var link=document.querySelector('a[href]');"
                        + "var href=link&&link.href||'';"
                        + "if(body.indexOf('Object moved')!==-1&&href.indexOf('" + LOCAL_AUTH_CALLBACK + "')===0){"
                        + "window.location.replace(href);"
                        + "}"
                    + "})();",
                    null
                );
            }
        });
    }
}
