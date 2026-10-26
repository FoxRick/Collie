Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Xaml
$references = @([System.Windows.Window].Assembly.Location, [System.Windows.Media.Brushes].Assembly.Location, [System.Windows.Threading.Dispatcher].Assembly.Location, [System.Xaml.XamlReader].Assembly.Location)
Add-Type -ReferencedAssemblies $references -TypeDefinition @"
using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using System.Windows.Threading;
public static class CollieComputerFixture {
    public static void Run() {
        var window = new Window { Title = "Collie disposable WPF acceptance", Width = 450, Height = 320, ShowActivated = false };
        var panel = new StackPanel();
        var draft = new TextBox { Height = 40 };
        AutomationProperties.SetName(draft, "Disposable draft");
        var button = new Button { Content = "Apply locally" };
        var status = new TextBlock { Text = "Waiting locally" };
        int clicks = 0;
        button.Click += (s, e) => { clicks++; status.Text = "Applied locally"; button.Content = "Applied locally " + clicks; };
        panel.Children.Add(draft); panel.Children.Add(button); panel.Children.Add(status);
        window.Content = panel;
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(120) };
        timer.Tick += (s, e) => window.Close(); timer.Start();
        window.ShowDialog();
    }
}
"@
[CollieComputerFixture]::Run()
