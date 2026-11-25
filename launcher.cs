//知更 桌面启动器：找到 Python，静默拉起 app.py（原生窗口 + 本地服务）。
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

static class Program
{
    const int Port = 8899;

    static string BaseDir
    {
        get { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'); }
    }

    [STAThread]
    static void Main()
    {
        string py = FindPython();
        if (py == null)
        {
            MessageBox.Show("找不到 Python。请安装 Python 并勾选 Add to PATH。",
                "知更", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        bool already = PortOpen();
        try
        {
            var psi = new ProcessStartInfo(py, "\"" + Path.Combine(BaseDir, "app.py") + "\"");
            psi.WorkingDirectory = BaseDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            Process.Start(psi);
        }
        catch (Exception e)
        {
            MessageBox.Show("启动失败：" + e.Message, "知更",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        if (already) return;   // 已经有一个在跑，新窗口会直接连上去

        for (int i = 0; i < 40 && !PortOpen(); i++) Thread.Sleep(300);
        if (!PortOpen())
        {
            MessageBox.Show("知更 没能启动服务。\n\n先双击「启动.bat」看看报什么错。",
                "知更", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static bool PortOpen()
    {
        try
        {
            using (var c = new TcpClient())
            {
                IAsyncResult ar = c.BeginConnect("127.0.0.1", Port, null, null);
                if (!ar.AsyncWaitHandle.WaitOne(400)) return false;
                c.EndConnect(ar);
                return true;
            }
        }
        catch { return false; }
    }

    static string FindPython()
    {
        var psi = new ProcessStartInfo("cmd.exe", "/c py -c \"import sys;print(sys.executable)\"");
        psi.UseShellExecute = false;
        psi.RedirectStandardOutput = true;
        psi.CreateNoWindow = true;
        try
        {
            using (var p = Process.Start(psi))
            {
                string outp = p.StandardOutput.ReadToEnd().Trim();
                p.WaitForExit(5000);
                if (outp.Length > 0)
                {
                    string dir = Path.GetDirectoryName(outp);
                    string pw = Path.Combine(dir, "pythonw.exe");
                    if (File.Exists(pw)) return pw;
                    if (File.Exists(outp)) return outp;
                }
            }
        }
        catch { }

        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] vers = { "314", "313", "312", "311", "310" };
        foreach (string v in vers)
        {
            string p = Path.Combine(local, "Programs", "Python", "Python" + v, "pythonw.exe");
            if (File.Exists(p)) return p;
        }
        return null;
    }
}
