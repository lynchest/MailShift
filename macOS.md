# MailShift - macOS Uyum İnceleme Raporu

MailShift projesinin kaynak kodu incelendiğinde, projenin büyük ölçüde işletim sisteminden bağımsız (cross-platform) çalışacak şekilde tasarlandığı görülmektedir. Python'un standart kütüphaneleri ve cross-platform paketler (ör. `rich`, `requests`, `psutil`) sayesinde temel işlevler macOS üzerinde sorunsuz çalışır. Ancak, bazı özel özellikler ve kolaylık sağlayan entegrasyonlar işletim sistemine özgü farklılıklar göstermektedir.

Aşağıda macOS (Darwin) ortamı için detaylı uyumluluk analizini bulabilirsiniz:

## 1. Terminal ve Çıktı Yönetimi (UI/CLI)
- **Windows Düzeltmeleri:** `src/mailshift/main.py` dosyasında Windows terminalinin UTF-8 karakterleri ve renkleri doğru göstermesi için özel `sys.platform == "win32"` kontrolleri ve `sys.stdout`/`sys.stderr` yeniden yapılandırmaları mevcuttur.
- **macOS Uyumluluğu:** macOS terminalleri (Terminal.app, iTerm2 vb.) yerleşik olarak UTF-8 ve ANSI renk kodlarını destekler. Bu nedenle, Windows'a özgü bu yamalara macOS'ta ihtiyaç duyulmaz ve `rich` kütüphanesi macOS üzerinde tam performansla, sorunsuz bir görsel deneyim sunar.

## 2. Otomatik Kurulumlar (Auto-Installers)
- **LM Studio ve Ollama Kurulumu:** Projede, LLM arka uçları eksik olduğunda kullanıcıya otomatik kurulum öneren bir yapı bulunmaktadır. Ancak bu kurulum işlemleri (örneğin `winget` kullanımı veya `install.ps1` betiği) tamamen Windows'a özgüdür.
- **macOS Uyumluluğu:** `src/mailshift/ui/cli.py` içerisinde `sys.platform != "win32"` kontrolü yapılarak macOS ve Linux kullanıcılarına otomatik kurulum sunulmaz. macOS kullanıcıları, eksik bağımlılıklar durumunda manuel kurulum yapmaları gerektiğine dair bilgilendirilir. İlerleyen dönemde `brew` (Homebrew) entegrasyonu eklenerek macOS için otomatik kurulum desteği sağlanabilir.

## 3. Süreç (Process) Yönetimi ve Arka Plan Servisleri
- **Ollama ve LM Studio Başlatma/Durdurma:** Arka uç servislerini başlatmak için `subprocess.Popen` kullanılır. Süreçlerin bağımsız çalışması için Windows'ta `creationflags=0x08000000` kullanılırken, macOS (ve diğer Unix sistemlerinde) `start_new_session=True` parametresi kullanılarak işletim sistemi bazında doğru süreç izolasyonu sağlanır.
- **Süreç Sonlandırma:** `psutil` kullanılarak isme göre süreç sonlandırma (`_kill_process_by_name`) işlemi platform bağımsızdır ve macOS'ta sorunsuz çalışır. Ancak Windows'a özgü bazı ek süreç adları (ör. `ollama_llama_server`) sadece Windows'ta aranır.

## 4. Donanım Hızlandırma ve Worker (İş Parçacığı) Hesaplaması
- **GPU Algılama:** `src/mailshift/utils/hardware.py` dosyasında donanım algılama işlemleri platforma göre ayrıştırılmıştır.
- **Apple Silicon (M1/M2/M3 vb.):** `platform.system() == "Darwin" ve platform.machine() == "arm64"` kontrolü ile Apple Silicon cihazlar başarıyla tespit edilir. Birleşik bellek (Unified Memory) mimarisi göz önüne alınarak, toplam RAM ve kullanılabilir RAM üzerinden tahmini bir VRAM (Metal API üzerinden) hesaplaması yapılır.
- **Çıkarımlar:** Apple Silicon cihazlarda `has_gpu=True` olarak işaretlenir ve model yükü tahmini ile paralel worker (iş parçacığı) sayısı, birleşik bellek kapasitesine göre optimal seviyede hesaplanır. Intel tabanlı Mac'ler ise muhtemelen standart CPU (veya genel iGPU) kurallarına tabi tutulur, çünkü NVIDIA/AMD sorguları öncelikle Windows ve Linux araçlarına (ör. `nvidia-smi` veya WMI) odaklanmıştır.

## 5. Kimlik Bilgisi Yönetimi (Keyring)
- **Güvenli Saklama:** Kimlik bilgileri (IMAP şifreleri vb.) `keyring` kütüphanesi kullanılarak işletim sisteminin yerel kimlik yöneticisinde saklanır.
- **macOS Uyumluluğu:** `keyring` kütüphanesi macOS üzerinde yerleşik "Keychain Access" (Anahtar Zinciri Erişimi) ile doğrudan entegredir. E-posta ve şifreler macOS'un güvenli kasasında şifrelenmiş olarak saklanır ve sorunsuz çalışır.

## Özet ve Öneriler
MailShift, macOS ortamında **tamamen işlevseldir** ve temel hiçbir özellikte kayıp yaşanmaz. Apple Silicon (M serisi) cihazlar için özel donanım algılama mantığı da projeye dahil edilmiştir.

**Geliştirme Önerileri:**
1. **Homebrew Entegrasyonu:** `winget`'e benzer şekilde, macOS için LM Studio ve Ollama otomatik kurulumları Homebrew (`brew install --cask lm-studio`, `brew install ollama`) kullanılarak eklenebilir.
2. **Intel Mac'ler için GPU Algılama:** Eski nesil Intel Mac'lerdeki AMD veya Intel ekran kartları için ek algılama komutları (ör. `system_profiler SPDisplaysDataType`) eklenebilir.
