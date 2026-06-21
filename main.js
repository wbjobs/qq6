const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let mainWindow;
let backendProcess;
const BACKEND_PORT = 8000;
const BACKEND_URL = `http://localhost:${BACKEND_PORT}`;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    title: '室内空气质量监测系统',
    icon: null,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      enableRemoteModule: true,
      webSecurity: false
    }
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile('index.html');

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const backendScript = path.join(__dirname, 'backend', 'app.py');
    
    if (!fs.existsSync(backendScript)) {
      console.error('后端脚本不存在:', backendScript);
      reject(new Error('后端脚本不存在'));
      return;
    }

    const pythonArgs = [backendScript];
    const isWindows = process.platform === 'win32';
    const pythonCmd = isWindows ? 'python' : 'python3';

    backendProcess = spawn(pythonCmd, pythonArgs, {
      cwd: __dirname,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PORT: BACKEND_PORT.toString()
      }
    });

    backendProcess.stdout.on('data', (data) => {
      const output = data.toString().trim();
      console.log('[Backend STDOUT]:', output);
      
      if (output.includes('Uvicorn running on') || 
          output.includes('Application startup complete')) {
        console.log('后端服务启动成功');
        resolve();
      }
    });

    backendProcess.stderr.on('data', (data) => {
      const output = data.toString().trim();
      console.error('[Backend STDERR]:', output);
      
      if (output.includes('Uvicorn running on') || 
          output.includes('Application startup complete')) {
        console.log('后端服务启动成功');
        resolve();
      }
      
      if (output.includes('Address already in use') ||
          output.includes('Error')) {
        console.warn('端口可能已被占用，尝试连接现有服务...');
        setTimeout(resolve, 1000);
      }
    });

    backendProcess.on('error', (err) => {
      console.error('启动后端进程失败:', err);
      reject(err);
    });

    backendProcess.on('close', (code) => {
      console.log(`后端进程退出，代码: ${code}`);
      backendProcess = null;
    });

    setTimeout(() => {
      console.log('等待后端超时，假设已启动...');
      resolve();
    }, 8000);
  });
}

function stopBackend() {
  if (backendProcess) {
    console.log('正在停止后端服务...');
    backendProcess.kill('SIGTERM');
    backendProcess = null;
  }
}

app.whenReady().then(async () => {
  console.log('应用启动中...');
  
  try {
    await startBackend();
  } catch (error) {
    console.warn('后端启动失败，继续启动前端:', error.message);
  }
  
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopBackend();
});

ipcMain.handle('get-backend-url', () => {
  return BACKEND_URL;
});

ipcMain.handle('restart-backend', async () => {
  stopBackend();
  try {
    await startBackend();
    return { success: true, url: BACKEND_URL };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

process.on('SIGINT', () => {
  stopBackend();
  process.exit(0);
});

process.on('SIGTERM', () => {
  stopBackend();
  process.exit(0);
});
