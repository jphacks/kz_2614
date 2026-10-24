package com.example.jphackskz2614androidver;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.os.Bundle;
import android.util.Base64;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.util.concurrent.*;
import org.json.JSONObject;

/** Native microphone/speaker client. No WebView or HTTP transport. */
public class MainActivity extends Activity {
    private EditText host, port;
    private TextView status;
    private Button start;
    private volatile Call call;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        File[] stale = getCacheDir().listFiles((dir, name) -> name.startsWith("voice-reply-"));
        if (stale != null) for (File file : stale) file.delete();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 48, 32, 32);
        host = new EditText(this);
        host.setSingleLine(true);
        host.setHint("サーバーIPアドレス");
        host.setText(getPreferences(0).getString("tcp_host", "192.168.137.201"));
        port = new EditText(this);
        port.setInputType(2);
        port.setText(getPreferences(0).getString("tcp_port", "8766"));
        status = new TextView(this);
        status.setText("音声通話・未接続");
        start = new Button(this);
        start.setText("通話開始");
        Button stop = new Button(this);
        stop.setText("停止");
        for (android.view.View view : new android.view.View[]{host, port, start, stop, status}) layout.addView(view);
        setContentView(layout);
        start.setOnClickListener(v -> {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1);
            else begin();
        });
        stop.setOnClickListener(v -> stopCall());
    }

    @Override public void onRequestPermissionsResult(int r, String[] p, int[] grants) {
        super.onRequestPermissionsResult(r, p, grants);
        if (r == 1 && grants.length > 0 && grants[0] == PackageManager.PERMISSION_GRANTED) begin();
        else status.setText("マイクの許可が必要です。");
    }

    private void begin() {
        if (call != null) return;
        try {
            int number = Integer.parseInt(port.getText().toString());
            String address = host.getText().toString().trim();
            if (address.isEmpty() || number < 1 || number > 65535) throw new IllegalArgumentException();
            getPreferences(0).edit().putString("tcp_host", address).putString("tcp_port", "" + number).apply();
            Call next = new Call(address, number);
            call = next;
            start.setEnabled(false);
            status.setText("接続中…");
            new Thread(next::run, "voice-connection").start();
        } catch (Exception e) { status.setText("IPアドレスとポートを確認してください。"); }
    }

    private void stopCall() {
        Call old = call;
        call = null;
        if (old != null) old.close();
        start.setEnabled(true);
        status.setText("停止しました");
    }
    @Override protected void onStop() { stopCall(); super.onStop(); }

    private final class Call {
        final String address;
        final int number;
        final Socket socket = new Socket();
        final Object writeLock = new Object();
        final ArrayBlockingQueue<JSONObject> replies = new ArrayBlockingQueue<>(8);
        volatile boolean closed;
        volatile AudioRecord recorder;
        volatile MediaPlayer player;
        volatile Thread playbackThread;
        DataOutputStream output;
        String session;
        int generation;

        Call(String a, int n) { address = a; number = n; }
        void send(JSONObject value) throws Exception {
            byte[] bytes = value.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
            synchronized (writeLock) {
                if (closed) throw new IOException("stopped");
                output.writeInt(bytes.length);
                output.write(bytes);
                output.flush();
            }
        }
        JSONObject read(DataInputStream input) throws Exception {
            int size = input.readInt();
            if (size < 1 || size > 24 * 1024 * 1024) throw new IOException("packet size");
            byte[] data = new byte[size];
            input.readFully(data);
            return new JSONObject(new String(data, java.nio.charset.StandardCharsets.UTF_8));
        }
        void run() {
            try {
                socket.connect(new InetSocketAddress(address, number), 5000);
                socket.setTcpNoDelay(true);
                socket.setSoTimeout(15000);
                output = new DataOutputStream(socket.getOutputStream());
                DataInputStream input = new DataInputStream(socket.getInputStream());
                send(new JSONObject().put("type", "start").put("version", 1));
                JSONObject hello = read(input);
                if (!hello.getString("type").equals("session")) throw new IOException(hello.optString("message"));
                session = hello.getString("session_id");
                generation = hello.getInt("generation");
                if (closed) return;
                playbackThread = new Thread(this::playReplies, "voice-playback");
                playbackThread.start();
                new Thread(this::capture, "voice-microphone").start();
                while (!closed) {
                    JSONObject event = read(input);
                    String kind = event.getString("type");
                    if (kind.equals("error")) throw new IOException(event.optString("message", "サーバーエラー"));
                    if (!session.equals(event.optString("session_id")) || generation != event.optInt("generation", -1)) continue;
                    if (kind.equals("state") && event.has("error")) throw new IOException(event.optString("message"));
                    if (kind.equals("state")) update("通話中・" + event.optString("stage", "listening"));
                    if (kind.equals("audio_chunk") || kind.equals("acknowledgement") || kind.equals("complete")) {
                        // Bound both event count and audio memory. Large chunks fail explicitly.
                        if (event.optString("audio_base64").length() > 2 * 1024 * 1024 || !replies.offer(event))
                            throw new IOException("再生が追いつきません。通話を開始し直してください。");
                    }
                }
            } catch (Exception e) { fail(e); }
            finally { close(); }
        }
        void capture() {
            AudioRecord mic = null;
            AcousticEchoCanceler echo = null;
            try {
                int minimum = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                if (minimum <= 0) throw new IOException("16kHz録音に対応していません。");
                mic = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, 16000,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(minimum, 16384));
                if (mic.getState() != AudioRecord.STATE_INITIALIZED) throw new IOException("マイクを開始できません。");
                synchronized (this) {
                    if (closed) return;
                    recorder = mic;
                    if (AcousticEchoCanceler.isAvailable()) {
                        echo = AcousticEchoCanceler.create(mic.getAudioSessionId());
                        if (echo != null) echo.setEnabled(true);
                    }
                    mic.startRecording();
                }
                byte[] frame = new byte[8192];
                long sequence = 0;
                while (!closed) {
                    int count = mic.read(frame, 0, frame.length, AudioRecord.READ_BLOCKING);
                    if (count <= 0 || count % 2 != 0) throw new IOException("マイクが切断されました。");
                    send(new JSONObject().put("type", "pcm").put("sequence", sequence++)
                        .put("audio_base64", Base64.encodeToString(frame, 0, count, Base64.NO_WRAP)));
                }
            } catch (Exception e) { fail(e); }
            finally {
                synchronized (this) { recorder = null; }
                if (echo != null) echo.release();
                if (mic != null) mic.release();
            }
        }
        void notifyPlayback(String turn, String event) throws Exception {
            send(new JSONObject().put("type", "playback").put("turn_id", turn).put("event", event));
        }
        void playReplies() {
            String activeTurn = null;
            try {
                while (!closed) {
                    JSONObject event = replies.take();
                    String turn = event.getString("turn_id");
                    if (event.getString("type").equals("complete")) {
                        if (turn.equals(activeTurn)) notifyPlayback(turn, "ended");
                        activeTurn = null;
                        continue;
                    }
                    byte[] audio = Base64.decode(event.getString("audio_base64"), Base64.DEFAULT);
                    File temporary = File.createTempFile("voice-reply-", ".audio", getCacheDir());
                    MediaPlayer next = new MediaPlayer();
                    try {
                        try (FileOutputStream file = new FileOutputStream(temporary)) { file.write(audio); }
                        CountDownLatch finished = new CountDownLatch(1);
                        java.util.concurrent.atomic.AtomicBoolean failed = new java.util.concurrent.atomic.AtomicBoolean();
                        next.setOnCompletionListener(p -> finished.countDown());
                        next.setOnErrorListener((p, what, extra) -> { failed.set(true); finished.countDown(); return true; });
                        next.setDataSource(temporary.getAbsolutePath());
                        next.prepare();
                        synchronized (this) {
                            if (closed) return;
                            player = next;
                            next.start();
                        }
                        if (!turn.equals(activeTurn)) { notifyPlayback(turn, "started"); activeTurn = turn; }
                        if (!finished.await(120, TimeUnit.SECONDS) || failed.get()) throw new IOException("音声を再生できません。");
                    } finally {
                        synchronized (this) { player = null; next.release(); }
                        temporary.delete();
                    }
                }
            } catch (Exception e) {
                if (!closed && activeTurn != null) try { notifyPlayback(activeTurn, "failed"); } catch (Exception ignored) { }
                fail(e);
            }
        }
        void update(String message) { runOnUiThread(() -> { if (call == this && !closed) status.setText(message); }); }
        void fail(Exception error) {
            if (closed) return;
            close();
            runOnUiThread(() -> {
                if (call == this) {
                    call = null;
                    start.setEnabled(true);
                    status.setText("通話終了: " + error.getMessage() + " 接続を確認して開始し直してください。");
                }
            });
        }
        synchronized void close() {
            if (closed) return;
            closed = true;
            // Closing the socket cancels both directions; server retires this session.
            try { socket.close(); } catch (IOException ignored) { }
            if (recorder != null) try { recorder.stop(); } catch (IllegalStateException ignored) { }
            if (player != null) try { player.stop(); } catch (IllegalStateException ignored) { }
            if (playbackThread != null) playbackThread.interrupt();
            replies.clear();
        }
    }
}
