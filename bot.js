const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, Browsers, delay } = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

async function connectToWhatsApp() {
    console.log("Mempersiapkan Client WhatsApp (Baileys)...");

    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');

    // Setup socket connection
    const sock = makeWASocket({
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: "silent" }),
        browser: Browsers.macOS('Desktop'), // Bypass WAF/Fingerprinting WhatsApp
        syncFullHistory: false // Meringankan beban awal koneksi
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr) {
            console.log('\n=========================================');
            console.log('SCAN QR CODE DI BAWAH INI DENGAN WHATSAPP');
            console.log('=========================================');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'close') {
            const shouldReconnect = lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('\n[-] Koneksi terputus, reconnecting:', shouldReconnect);
            
            if (shouldReconnect) {
                connectToWhatsApp();
            } else {
                console.log('\n[-] Anda telah logout. Hapus folder auth_info_baileys dan jalankan ulang untuk scan baru.');
            }
        } else if (connection === 'open') {
            console.log('\n✅ Client is ready! Bot WhatsApp Asli sudah aktif via WebSockets!');
        }
    });

    sock.ev.on('messages.upsert', async (m) => {
        const msg = m.messages[0];
        
        // Abaikan pesan dari diri sendiri atau event non-pesan
        if (!msg.message || msg.key.fromMe) return;

        const sender = msg.key.remoteJid;
        
        // Ekstraksi text dari berbagai format JSON pesan Baileys
        const text = msg.message.conversation || 
                     msg.message.extendedTextMessage?.text || 
                     msg.message.imageMessage?.caption || 
                     "";

        if (text) {
            console.log(`\n[+] Pesan WA dari ${sender.replace('@s.whatsapp.net', '')}: ${text}`);

            try {
                // Forward pesan ke Python API (Flask Server)
                const response = await axios.post('http://127.0.0.1:5000/api/chat', {
                    sender: sender,
                    message: text
                });
                
                if (response.data && response.data.reply) {
                    // Evasion: Simulasi delay & status "sedang mengetik" agar tidak terdeteksi bot (Anti-Spam Bypass)
                    await sock.sendPresenceUpdate('composing', sender);
                    await delay(1500 + Math.random() * 1000); // Delay acak 1.5 - 2.5 detik
                    await sock.sendPresenceUpdate('paused', sender);

                    // Reply langsung ke pengirim via Socket
                    await sock.sendMessage(sender, { text: response.data.reply });
                    console.log(`[+] Balasan terkirim!`);
                }
            } catch (error) {
                console.error('[-] Error menghubungi server Python:', error.message);
            }
        }
    });
}

// Inisialisasi awal
connectToWhatsApp();
