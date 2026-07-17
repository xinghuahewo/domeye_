import axios from 'axios';
import baseUrl from '/@/api';

const buildHeaders = (extraHeaders: Record<string, string> = {}) => {
	const headers: Record<string, string> = { ...extraHeaders };
	const tokenItem = localStorage.getItem('token');
	if (tokenItem) {
		const token = JSON.parse(tokenItem);
		if (token?.token) headers.Authorization = `Bearer ${token.token}`;
	}
	return headers;
};

export const listDataQueryTasks = async () => {
	const response = await axios.get(`${baseUrl}data-query/tasks`, {
		headers: buildHeaders(),
	});
	return response.data;
};

export const getDataQueryTaskDetail = async (taskId: string) => {
	const response = await axios.get(`${baseUrl}data-query/tasks/${taskId}`, {
		headers: buildHeaders(),
	});
	return response.data;
};

export const deleteDataQueryTask = async (taskId: string) => {
	const response = await axios.delete(`${baseUrl}data-query/tasks/${taskId}`, {
		headers: buildHeaders(),
	});
	return response.data;
};

export const parseDataQueryTask = async (formData: FormData) => {
	const response = await axios.post(`${baseUrl}data-query/tasks/parse`, formData, {
		headers: buildHeaders(),
	});
	return response.data;
};

export const generateDataQueryExport = async (taskId: string, selectedFieldIds: string[]) => {
	return await axios.post(
		`${baseUrl}data-query/tasks/${taskId}/generate`,
		{ selected_field_ids: selectedFieldIds },
		{
			headers: buildHeaders({ 'Content-Type': 'application/json' }),
			responseType: 'blob',
		}
	);
};

export const getDataQueryPreview = async (taskId: string, selectedFieldIds: string[]) => {
	const response = await axios.post(
		`${baseUrl}data-query/tasks/${taskId}/preview`,
		{ selected_field_ids: selectedFieldIds },
		{
			headers: buildHeaders({ 'Content-Type': 'application/json' }),
		}
	);
	return response.data;
};
