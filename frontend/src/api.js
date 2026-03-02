import axios from 'axios';

const API = axios.create({ baseURL: 'http://127.0.0.1:8000' });

export const getProjects = () => API.get('/projects').then(r => r.data);
export const getProject = (name) => API.post('/project/get', { name }).then(r => r.data);
export const saveProject = (name, data) => API.post('/project/save', { name, data });
export const deleteProject = (name) => API.post('/project/delete', { name });
export const getConfig = () => API.get('/config').then(r => r.data);
export const saveConfig = (data) => API.post('/config/save', { name: '系统配置', data });

export default API;
