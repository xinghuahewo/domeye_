<template>
	<div class="layout-pd data-query-container">
		<el-card shadow="hover" class="task-card">
			<template #header>
				<div class="card-header">
					<div>
						<div class="card-title">数据查询任务</div>
						<div class="card-subtitle">上传标准业务字段文件，选择可回填的数据库字段并导出整合结果。</div>
					</div>
					<el-button type="primary" @click="openCreateDialog">添加新任务</el-button>
				</div>
			</template>

			<el-table
				:data="taskList"
				v-loading="listLoading"
				border
				row-key="task_id"
				empty-text="暂无任务，先添加一个数据查询任务"
				table-layout="fixed"
				class="task-table"
			>
				<el-table-column prop="task_name" label="任务名称" min-width="140" show-overflow-tooltip />
				<el-table-column prop="file_name" label="文件名" min-width="160" show-overflow-tooltip />
				<el-table-column prop="row_count" label="数据行数" width="96" />
				<el-table-column label="识别字段" min-width="180">
					<template #default="{ row }">
						<div class="tag-group">
							<el-tag v-for="field in row.recognized_fields" :key="field" size="small" type="info">
								{{ field }}
							</el-tag>
							<span v-if="!row.recognized_fields?.length" class="muted-text">未识别</span>
						</div>
					</template>
				</el-table-column>
				<el-table-column label="补充字段" min-width="240">
					<template #default="{ row }">
						<div v-if="row.matched_database_fields?.length" class="field-scroll">
							<el-checkbox-group
								:model-value="getSelectedFieldIds(row.task_id)"
								class="field-checkbox-group"
								@change="handleCheckboxGroupChange(row.task_id, $event)"
							>
								<el-checkbox-button
									v-for="field in row.matched_database_fields"
									:key="field.field_id"
									:label="field.field_id"
									:title="field.label"
									class="field-checkbox"
									size="small"
								>
									{{ formatFieldDisplay(field) }}
								</el-checkbox-button>
							</el-checkbox-group>
						</div>
						<span v-if="!row.matched_database_fields?.length" class="muted-text">暂无可补充字段</span>
					</template>
				</el-table-column>
				<el-table-column label="状态" width="100">
					<template #default="{ row }">
						{{ formatStatus(row.status) }}
					</template>
				</el-table-column>
				<el-table-column label="创建时间" width="168" show-overflow-tooltip>
					<template #default="{ row }">
						{{ formatBeijingTime(row.created_at) }}
					</template>
				</el-table-column>
				<el-table-column label="操作" width="156">
					<template #default="{ row }">
							<div class="action-buttons">
								<el-button
									link
									type="primary"
									:loading="downloadingTaskId === row.task_id"
									@click.stop="downloadTask(row)"
								>
									下载
								</el-button>
								<el-button link type="danger" @click.stop="handleDeleteTask(row)">删除</el-button>
							</div>
						</template>
					</el-table-column>
			</el-table>
		</el-card>

		<el-dialog v-model="createDialogVisible" title="添加新任务" width="520px" @closed="resetCreateDialog">
			<el-form label-width="90px">
				<el-form-item label="任务名称">
					<el-input v-model="createForm.taskName" placeholder="默认使用文件名" clearable />
				</el-form-item>
				<el-form-item label="上传文件">
					<el-upload
						:key="uploadKey"
						v-model:file-list="fileList"
						:auto-upload="false"
						:limit="1"
						:on-change="handleFileChange"
						:on-remove="handleFileRemove"
						:before-upload="beforeUpload"
						accept=".csv,.xls,.xlsx"
						drag
						class="upload-area"
					>
						<el-icon class="el-icon--upload"><UploadFilled /></el-icon>
						<div class="el-upload__text">拖拽文件到此，或 <em>点击选择文件</em></div>
						<template #tip>
							<div class="el-upload__tip">支持 CSV / XLS / XLSX，字段建议使用 asn、prefix、ip、country、source、s_time 等标准列名。</div>
						</template>
					</el-upload>
				</el-form-item>
			</el-form>
			<template #footer>
				<div class="dialog-footer">
					<el-button @click="createDialogVisible = false">取消</el-button>
					<el-button type="primary" :loading="createLoading" @click="submitCreateTask">解析文件</el-button>
				</div>
			</template>
		</el-dialog>
	</div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox, type UploadFile, type UploadFiles, type UploadRawFile, type UploadUserFile } from 'element-plus';
import { UploadFilled } from '@element-plus/icons-vue';
import {
	deleteDataQueryTask,
	generateDataQueryExport,
	listDataQueryTasks,
	parseDataQueryTask,
} from '/@/api/dataQuery';

type TaskFieldSummary = {
	field_id: string;
	field_name: string;
	label: string;
	table_label: string;
	sample_hit_count: number;
	sample_total_count: number;
};

type TaskSummary = {
	task_id: string;
	task_name: string;
	file_name: string;
	row_count: number;
	recognized_fields: string[];
	matched_field_count: number;
	matched_database_fields: TaskFieldSummary[];
	selected_field_ids: string[];
	status: string;
	created_at: string;
	generated_at: string;
};

const normalizeTask = (task: Partial<TaskSummary>): TaskSummary => ({
	task_id: String(task.task_id || ''),
	task_name: String(task.task_name || ''),
	file_name: String(task.file_name || ''),
	row_count: Number(task.row_count || 0),
	recognized_fields: Array.isArray(task.recognized_fields) ? task.recognized_fields.map((item) => String(item)) : [],
	matched_field_count: Number(task.matched_field_count || 0),
	matched_database_fields: Array.isArray(task.matched_database_fields)
		? task.matched_database_fields.map((field) => ({
				field_id: String(field?.field_id || ''),
				field_name: String(field?.field_name || ''),
				label: String(field?.label || field?.field_name || ''),
				table_label: String(field?.table_label || ''),
				sample_hit_count: Number(field?.sample_hit_count || 0),
				sample_total_count: Number(field?.sample_total_count || 0),
		  }))
		: [],
	selected_field_ids: Array.isArray(task.selected_field_ids) ? task.selected_field_ids.map((item) => String(item)) : [],
	status: String(task.status || ''),
	created_at: String(task.created_at || ''),
	generated_at: String(task.generated_at || ''),
});

const listLoading = ref(false);
const createLoading = ref(false);
const downloadingTaskId = ref('');
const createDialogVisible = ref(false);
const taskList = ref<TaskSummary[]>([]);
const fileList = ref<UploadUserFile[]>([]);
const selectedRawFile = ref<UploadRawFile | null>(null);
const selectedFieldsMap = ref<Record<string, string[]>>({});
const uploadKey = ref(0);

const createForm = reactive({
	taskName: '',
});

const showError = (error: any, fallbackMessage: string) => {
	const message = error?.response?.data?.msg || fallbackMessage;
	ElMessage.error(message);
};

const resolveDefaultFieldIds = (task: TaskSummary) => {
	if (task.selected_field_ids?.length) return [...task.selected_field_ids];
	return task.matched_database_fields
		.filter((field) => (field.sample_hit_count || 0) > 0)
		.map((field) => field.field_id);
};

const syncSelectedFields = (tasks: TaskSummary[]) => {
	const nextMap: Record<string, string[]> = {};
	tasks.forEach((task) => {
		const validFieldIds = new Set(task.matched_database_fields.map((field) => field.field_id));
		const hasLocalSelection = Object.prototype.hasOwnProperty.call(selectedFieldsMap.value, task.task_id);
		const currentSelection = selectedFieldsMap.value[task.task_id]?.filter((fieldId) => validFieldIds.has(fieldId)) || [];
		nextMap[task.task_id] = hasLocalSelection ? currentSelection : resolveDefaultFieldIds(task);
	});
	selectedFieldsMap.value = nextMap;
};

const getSelectedFieldIds = (taskId: string) => selectedFieldsMap.value[taskId] || [];

const handleFieldSelectionChange = (taskId: string, fieldIds: string[]) => {
	selectedFieldsMap.value = {
		...selectedFieldsMap.value,
		[taskId]: fieldIds,
	};
};

const handleCheckboxGroupChange = (taskId: string, value: Array<string | number | boolean>) => {
	handleFieldSelectionChange(taskId, value.map((item) => String(item)));
};

const formatFieldDisplay = (field: TaskFieldSummary) => field.field_name || field.label || field.field_id;

const formatStatus = (status: string) => {
	if (status === 'generated') return '已完成';
	if (status === 'parsed') return '进行中';
	return status || '-';
};

const formatBeijingTime = (value: string) => {
	if (!value) return '-';
	const match = value.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
	if (!match) return value;
	const [, year, month, day, hour, minute, second] = match;
	const utcMs = Date.UTC(
		Number(year),
		Number(month) - 1,
		Number(day),
		Number(hour),
		Number(minute),
		Number(second),
	);
	const beijingDate = new Date(utcMs + 8 * 60 * 60 * 1000);
	const parts = [
		beijingDate.getUTCFullYear(),
		String(beijingDate.getUTCMonth() + 1).padStart(2, '0'),
		String(beijingDate.getUTCDate()).padStart(2, '0'),
	];
	const timeParts = [
		String(beijingDate.getUTCHours()).padStart(2, '0'),
		String(beijingDate.getUTCMinutes()).padStart(2, '0'),
		String(beijingDate.getUTCSeconds()).padStart(2, '0'),
	];
	return `${parts.join('-')} ${timeParts.join(':')}`;
};

const loadTaskList = async () => {
	listLoading.value = true;
	try {
		const result = await listDataQueryTasks();
		taskList.value = Array.isArray(result.data) ? result.data.map((task: Partial<TaskSummary>) => normalizeTask(task)) : [];
		syncSelectedFields(taskList.value);
	} catch (error) {
		showError(error, '任务列表加载失败');
	} finally {
		listLoading.value = false;
	}
};

const handleDeleteTask = async (row: TaskSummary) => {
	try {
		await ElMessageBox.confirm(`确认删除任务“${row.task_name}”吗？删除后不可恢复。`, '删除任务', {
			type: 'warning',
			confirmButtonText: '删除',
			cancelButtonText: '取消',
		});
	} catch {
		return;
	}

	try {
		await deleteDataQueryTask(row.task_id);
		ElMessage.success('任务已删除');
		const nextMap = { ...selectedFieldsMap.value };
		delete nextMap[row.task_id];
		selectedFieldsMap.value = nextMap;
		await loadTaskList();
	} catch (error) {
		showError(error, '任务删除失败');
	}
};

const openCreateDialog = () => {
	resetCreateDialog();
	createDialogVisible.value = true;
};

const resetCreateDialog = () => {
	createForm.taskName = '';
	fileList.value = [];
	selectedRawFile.value = null;
	uploadKey.value += 1;
};

const beforeUpload = () => false;

const handleFileChange = (file: UploadFile, files: UploadFiles) => {
	selectedRawFile.value = file.raw || null;
	fileList.value = files.slice(-1);
};

const handleFileRemove = () => {
	selectedRawFile.value = null;
};

const submitCreateTask = async () => {
	if (!selectedRawFile.value) {
		ElMessage.warning('请先选择上传文件');
		return;
	}

	const formData = new FormData();
	formData.append('file', selectedRawFile.value);
	if (createForm.taskName.trim()) formData.append('task_name', createForm.taskName.trim());

	createLoading.value = true;
	try {
		await parseDataQueryTask(formData);
		ElMessage.success('文件解析完成');
		createDialogVisible.value = false;
		await loadTaskList();
	} catch (error) {
		showError(error, '文件解析失败');
	} finally {
		createLoading.value = false;
	}
};

const resolveDownloadName = (contentDisposition?: string) => {
	if (!contentDisposition) return 'data_query_result.csv';
	const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
	if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1]);
	const plainMatch = contentDisposition.match(/filename="?([^"]+)"?/i);
	return plainMatch?.[1] || 'data_query_result.csv';
};

const downloadTask = async (row: TaskSummary) => {
	const selectedFieldIds = getSelectedFieldIds(row.task_id);
	if (!selectedFieldIds.length) {
		ElMessage.warning('请先勾选需要补充的数据库字段');
		return;
	}

	downloadingTaskId.value = row.task_id;
	try {
		const response = await generateDataQueryExport(row.task_id, selectedFieldIds);
		const blob = new Blob([response.data], { type: 'text/csv;charset=utf-8;' });
		const downloadUrl = window.URL.createObjectURL(blob);
		const link = document.createElement('a');
		link.href = downloadUrl;
		link.download = resolveDownloadName(response.headers['content-disposition']);
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);
		window.URL.revokeObjectURL(downloadUrl);
		ElMessage.success('整合结果已生成');
		await loadTaskList();
	} catch (error) {
		showError(error, '导出失败');
	} finally {
		downloadingTaskId.value = '';
	}
};

onMounted(async () => {
	await loadTaskList();
});
</script>

<style scoped lang="scss">
.data-query-container {
	display: flex;
	flex-direction: column;
	gap: 16px;
}

.task-card {
	border-radius: 16px;
}

.task-table {
	width: 100%;
}

.card-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 16px;
}

.card-title {
	font-size: 18px;
	font-weight: 600;
	color: #1f2937;
}

.card-subtitle {
	margin-top: 6px;
	color: #6b7280;
	font-size: 13px;
}

.tag-group {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	align-items: flex-start;
}

.field-checkbox-group {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	align-items: flex-start;
}

.field-scroll {
	max-height: 92px;
	overflow-y: auto;
	padding-right: 4px;
	overflow-x: hidden;
}

.field-checkbox {
	margin-right: 0;
	max-width: 100%;
}

:deep(.field-checkbox .el-checkbox-button__inner) {
	padding: 4px 8px;
	border-radius: 12px;
	font-size: 12px;
	line-height: 1.3;
	white-space: normal;
	word-break: break-all;
	max-width: 100%;
}

.muted-text {
	color: #9ca3af;
	font-size: 13px;
}

.action-buttons {
	display: flex;
	flex-wrap: wrap;
	gap: 8px;
}

.upload-area {
	width: 100%;
}

:deep(.task-table .el-table__cell) {
	vertical-align: top;
}

:deep(.task-table .cell) {
	white-space: normal;
	word-break: break-word;
}
</style>
