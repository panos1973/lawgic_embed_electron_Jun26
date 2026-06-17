'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  selectFolder: () => ipcRenderer.invoke('dialog:selectFolder'),
  startIngest: (folder) => ipcRenderer.invoke('pipeline:start', folder),
  retry: () => ipcRenderer.invoke('pipeline:retry'),
  cancel: () => ipcRenderer.send('pipeline:cancel'),
  getStatus: () => ipcRenderer.invoke('pipeline:status'),
  runDiag: () => ipcRenderer.invoke('pipeline:diag'),
  getReview: () => ipcRenderer.invoke('pipeline:review'),
  getSettings: () => ipcRenderer.invoke('settings:get'),
  saveSettings: (obj) => ipcRenderer.invoke('settings:save', obj),
  getAppVersion: () => ipcRenderer.invoke('app:version'),
  openLogs: () => ipcRenderer.invoke('logs:open'),
  resetState: () => ipcRenderer.invoke('state:reset'),
  listCollections: () => ipcRenderer.invoke('collections:list'),
  resetCollection: (key) => ipcRenderer.invoke('collections:reset', key),
  listLaws: () => ipcRenderer.invoke('laws:list'),
  inspectLaw: (collection, law) => ipcRenderer.invoke('law:inspect', { collection, law }),
  onPipelineEvent: (cb) => ipcRenderer.on('pipeline:event', (_e, o) => cb(o)),
  onPipelineDone: (cb) => ipcRenderer.on('pipeline:done', (_e, o) => cb(o)),
});
