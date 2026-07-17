<template>
	<div class="system-menu-container layout-pd">
		<div>
			<el-dialog v-model="state.newDialogVisible" title="新增用户" width="30%">
				<el-form ref="newruleFormRef" :model="newruleForm" :rules="newrules" label-width="80px" class="demo-ruleForm" size="default" status-icon>
					<el-form-item label="账号" prop="userid">
						<el-input v-model="newruleForm.userid" />
					</el-form-item>
					<el-form-item label="用户名" prop="username">
						<el-input v-model="newruleForm.username" />
					</el-form-item>
					<el-form-item label="密码" prop="password">
						<el-input show-password v-model="newruleForm.password" />
					</el-form-item>
					<el-form-item label="角色" prop="role">
						<el-select v-model="newruleForm.role">
							<el-option label="管理员" value="admin" />
							<el-option label="操作员" value="operator" />
							<el-option label="访客" value="guest" />
						</el-select>
					</el-form-item>
				</el-form>
				<template #footer>
					<span class="dialog-footer">
						<el-button @click="newresetForm(newruleFormRef)">取消</el-button>
						<el-button type="primary" @click="newsubmitForm(newruleFormRef)">确定</el-button>
					</span>
				</template>
			</el-dialog>
			<el-dialog v-model="state.editDialogVisible" title="编辑用户" width="30%">
				<el-form ref="editruleFormRef" :model="editruleForm" :rules="editrules" label-width="80px" class="demo-ruleForm" size="default" status-icon>
					<el-form-item label="账号" prop="userid">
						<el-input v-model="editruleForm.userid" disabled />
					</el-form-item>
					<el-form-item label="用户名" prop="username">
						<el-input v-model="editruleForm.username" />
					</el-form-item>
					<el-form-item label="密码" prop="password">
						<el-input show-password v-model="editruleForm.password" />
					</el-form-item>
					<el-form-item label="角色" prop="role">
						<template v-if="!state.editSelf">
							<el-select v-model="editruleForm.role">
								<el-option label="管理员" value="admin" />
								<el-option label="操作员" value="operator" />
								<el-option label="访客" value="guest" />
							</el-select>
						</template>
						<template v-else>
							<el-select v-model="editruleForm.role" disabled>
								<el-option label="管理员" value="admin" />
								<el-option label="操作员" value="operator" />
								<el-option label="访客" value="guest" />
							</el-select>
						</template>
					</el-form-item>
				</el-form>
				<template #footer>
					<span class="dialog-footer">
						<el-button @click="editresetForm(editruleFormRef)">取消</el-button>
						<el-button type="primary" @click="editsubmitForm(editruleFormRef)">确定</el-button>
					</span>
				</template>
			</el-dialog>
			<el-dialog v-model="state.deleteDialogVisible" title="删除用户" width="30%">
				<div>{{ `确认删除账号：${state.delete_userid}，用户名：${state.delete_username} 的用户吗？` }}</div>
				<template #footer>
					<span class="dialog-footer">
						<el-button @click="state.deleteDialogVisible = false">取消</el-button>
						<el-button type="primary" @click="confirmDelete()">确定</el-button>
					</span>
				</template>
			</el-dialog>
		</div>
		<el-card shadow="hover">
			<div class="system-menu-search mb15" style="display: flex">
				<el-input size="default" v-model="state.userid" placeholder="账号" style="max-width: 120px"> </el-input>
				<el-input size="default" v-model="state.username" placeholder="用户名" style="max-width: 120px" class="ml10"> </el-input>
				<el-select size="default" v-model="state.role" placeholder="角色" style="max-width: 120px" class="ml10">
					<el-option v-for="item in roles" :key="item.value" :label="item.label" :value="item.value" />
				</el-select>
				<el-input size="default" v-model="state.creatorid" placeholder="创建人账号" style="max-width: 120px" class="ml10"> </el-input>
				<el-input size="default" v-model="state.creatorname" placeholder="创建人用户名" style="max-width: 120px" class="ml10"> </el-input>
				<el-date-picker
					v-model="state.create_time"
					type="daterange"
					range-separator="-"
					start-placeholder="创建时间起点"
					end-placeholder="创建时间终点"
					class="ml10"
					size="default"
					style="max-width: 280px"
				/>
				<el-button size="default" type="primary" class="ml15" style="width: 80px" @click="search()"> 查询 </el-button>
				<el-button size="default" type="default" class="ml15" style="width: 80px" @click="resetData()"> 重置 </el-button>
				<el-button size="default" type="success" class="ml15" @click="state.newDialogVisible = true">新增用户</el-button>
			</div>
			<el-table
				ref="multipleTableRef"
				:data="state.data"
				v-loading="state.loading"
				style="width: 100%"
				row-key="index"
				@sort-change="handleSortChange"
				size="default"
				border
			>
				<el-table-column type="index" :index="(state.current_page - 1) * state.page_size + 1" label="序号" width="70" />
				<el-table-column prop="userid" sortable="custom" label="账号" />
				<el-table-column prop="username" sortable="custom" label="用户名" />
				<el-table-column prop="rolename" sortable="custom" label="角色">
					<template #default="scope">
						<el-popover effect="light" trigger="hover" placement="top" width="auto">
							<template #default>
								<div v-if="scope.row.rolename === '管理员'">管理员具有浏览、操作和用户管理权限</div>
								<div v-else-if="scope.row.rolename === '操作员'">操作员具有浏览和操作权限</div>
								<div v-else>访客具有浏览权限</div>
							</template>
							<template #reference>
								<el-tag v-if="scope.row.rolename === '管理员'" type="warning">{{ scope.row.rolename }}</el-tag>
								<el-tag v-else-if="scope.row.rolename === '操作员'" type="success">{{ scope.row.rolename }}</el-tag>
								<el-tag v-else>{{ scope.row.rolename }}</el-tag>
							</template>
						</el-popover>
					</template>
				</el-table-column>
				<el-table-column prop="creatorid" sortable="custom" label="创建人账号" />
				<el-table-column prop="creatorname" sortable="custom" label="创建人用户名" />
				<el-table-column prop="create_time" sortable="custom" label="创建时间" />
				<el-table-column label="操作" style="display: flex">
					<template #default="scope">
						<template v-if="scope.row.userid != userInfo.userid">
							<el-button size="default" type="primary" @click="handleEdit(scope.row, false)">编辑</el-button>
							<el-button size="default" type="danger" @click="handleDelete(scope.row)">删除</el-button>
						</template>
						<template v-else>
							<el-button size="default" type="primary" @click="handleEdit(scope.row, true)">编辑</el-button>
							<el-tooltip class="box-item" effect="light" content="不可删除当前用户" placement="top" size="large">
								<el-button size="default" type="info">删除</el-button>
							</el-tooltip>
						</template>
					</template>
				</el-table-column>
			</el-table>
			<div class="table-footer mt20">
				<el-pagination
					v-model:current-page="state.current_page"
					v-model:page-size="state.page_size"
					:pager-count="15"
					:total="state.total_cnt"
					:page-count="state.total_page"
					:page-sizes="[10, 50, 100, 200]"
					background
					layout="prev, pager, next, jumper, sizes, ->, total"
					@current-change="handleCurrentChange"
					@size-change="handleSizeChange"
				>
				</el-pagination>
			</div>
		</el-card>
	</div>
</template>

<script setup lang="ts" name="user">
import baseUrl from "/@/api";
import { onMounted, reactive, ref } from 'vue';
import request from '/@/utils/request';
import type { FormInstance, FormRules } from 'element-plus';
import { ElMessage, ElTable } from 'element-plus';
import { useUserInfo } from '/@/stores/userInfo';
import { storeToRefs } from 'pinia';
const multipleTableRef = ref<InstanceType<typeof ElTable>>();
//当前用户信息
const stores = useUserInfo();
const { userInfo } = storeToRefs(stores);
// 新增用户
const newruleFormRef = ref<FormInstance>();
const newruleForm = reactive({
	userid: '',
	username: '',
	password: 'default123',
	role: '',
});
const validatePassword = (rule: any, value: string, callback: any) => {
	const trim_value = value.trim().toLowerCase();
	if (trim_value.length < 6) {
		callback(new Error('密码至少为6位'));
	} else if (/[a-z]/.test(trim_value) && /[0-9]/.test(trim_value)) {
		callback();
	} else {
		callback(new Error('密码必须同时包含数字和字母'));
	}
};
const newrules = reactive<FormRules>({
	userid: [{ required: true, message: '账号不能为空', trigger: 'blur' }],
	username: [{ required: true, message: '用户名不能为空', trigger: 'blur' }],
	password: [{ required: true, validator: validatePassword, trigger: 'blur' }],
	role: [{ required: true, message: '角色不能为空', trigger: 'change' }],
});
const newsubmitForm = async (formEl: FormInstance | undefined) => {
	if (!formEl) return;
	await formEl.validate((valid, fields) => {
		if (valid) {
			register().then(() => {
				formEl.resetFields();
			});
			state.newDialogVisible = false;
		} else {
			console.log('error submit!', fields);
		}
	});
};
const newresetForm = (formEl: FormInstance | undefined) => {
	if (!formEl) return;
	formEl.resetFields();
	state.newDialogVisible = false;
};
const register = async () => {
	const registerResult = await request({
    // url: 'http://10.3.242.226:19746/register',
		url: baseUrl + 'register',
		method: 'post',
		data: {
			userid: newruleForm.userid,
			username: newruleForm.username,
			password: newruleForm.password,
			role: newruleForm.role,
		},
	});
	if (registerResult.status) {
		ElMessage.success('注册成功');
		getTableData();
	} else {
		const msg = registerResult.msg ? registerResult.msg : '注册失败';
		ElMessage.error(msg);
	}
};
//编辑用户
const editruleFormRef = ref<FormInstance>();
const editruleForm = reactive({
	userid: '',
	username: '',
	password: '',
	role: '',
});
const editrules = reactive<FormRules>({
	userid: [{ required: true, message: '账号不能为空', trigger: 'blur' }],
	username: [{ required: true, message: '用户名不能为空', trigger: 'blur' }],
	password: [{ required: true, validator: validatePassword, trigger: 'blur' }],
	role: [{ required: true, message: '角色不能为空', trigger: 'change' }],
});
const editsubmitForm = async (formEl: FormInstance | undefined) => {
	if (!formEl) return;
	await formEl.validate((valid, fields) => {
		if (valid) {
			update().then(() => {
				formEl.resetFields();
			});
			state.editDialogVisible = false;
		} else {
			console.log('error submit!', fields);
		}
	});
};
const editresetForm = (formEl: FormInstance | undefined) => {
	if (!formEl) return;
	formEl.resetFields();
	state.editDialogVisible = false;
};
const update = async () => {
	const updateResult = await request({
    // url: 'http://10.3.242.226:19746/admin_edit',
		url: baseUrl + 'admin_edit',
		method: 'put',
		data: {
			userid: editruleForm.userid,
			username: editruleForm.username,
			password: editruleForm.password,
			role: editruleForm.role,
		},
	});
	if (updateResult.status) {
		ElMessage.success(`用户：${editruleForm.userid} 修改成功`);
		getTableData();
	} else {
		const msg = updateResult.msg ? updateResult.msg : `用户：${editruleForm.userid} 修改失败`;
		ElMessage.error(msg);
	}
};
const handleEdit = (row, editSelf: boolean) => {
	editruleForm.userid = row.userid;
	editruleForm.username = row.username;
	editruleForm.role = row.role;
	editruleForm.password = row.password;
	state.editSelf = editSelf;
	state.editDialogVisible = true;
};
//删除用户
const handleDelete = (row) => {
	state.delete_userid = row.userid;
	state.delete_username = row.username;
	state.deleteDialogVisible = true;
};
const confirmDelete = async () => {
	try {
		const deleteResult = await request({
      // url: 'http://10.3.242.226:19746/admin_edit',
			url: baseUrl + 'admin_edit',
			method: 'delete',
			data: {
				userid: state.delete_userid,
			},
		});
		if (deleteResult.status) {
			ElMessage.success(`用户：${state.delete_userid} 删除成功`);
			getTableData();
		} else {
			const msg = deleteResult.msg ? deleteResult.msg : `用户：${state.delete_userid} 删除失败`;
			ElMessage.error(msg);
		}
	} catch (e) {
		console.log(e);
	} finally {
		state.deleteDialogVisible = false;
	}
};

const state = reactive({
	data: [] as Array<any>,
	loading: true,
	newDialogVisible: false,
	editDialogVisible: false,
	deleteDialogVisible: false,
	editSelf: false,
	current_page: 1,
	page_size: 10,
	total_page: 1,
	total_cnt: 0,
	userid: '',
	username: '',
	role: '',
	creatorid: '',
	creatorname: '',
	create_time: '',
	sort_mode: '',
	delete_userid: '',
	delete_username: '',
});
const mapRole = {
	admin: '管理员',
	operator: '操作员',
	guest: '访客',
};
const roles = [
	{
		value: 'admin',
		label: '管理员',
	},
	{
		value: 'operator',
		label: '操作员',
	},
	{
		value: 'guest',
		label: '访客',
	},
];
// Date转String
const dateToString = (date: any) => {
	let year = date.getFullYear();
	let month = (date.getMonth() + 1).toString();
	let day = date.getDate().toString();
	if (month.length < 2) {
		month = '0' + month;
	}
	if (day.length < 2) {
		day = '0' + day;
	}
	return `${year}-${month}-${day}`;
};
// 获取列表数据，初始化表格
const getTableData = async () => {
	state.loading = true;
	// 获取全部数据
	try {
		const res = await request({
      // url: 'http://10.3.242.226:19746/user_list',
			url: baseUrl + 'users',
			method: 'get',
			params: {
				page_num: state.current_page,
				page_size: state.page_size,
				userid: state.userid,
				username: state.username,
				role: state.role,
				creatorid: state.creatorid,
				creatorname: state.creatorname,
				create_time: state.create_time === '' ? '' : dateToString(state.create_time[0]) + '_' + dateToString(state.create_time[1]),
				sort_mode: state.sort_mode,
			},
		});
		res.data.forEach((item) => {
			item.rolename = mapRole[item.role];
		});
		state.total_page = res.total_page > 0 ? res.total_page : 1;
		state.total_cnt = res.record_count;
		state.data = res.data;
	} catch (e) {
		// ElMessage.warning('网络异常，请稍后再试');
	} finally {
		state.loading = false;
	}
};
const handleCurrentChange = (val: number) => {
	state.current_page = val;
	getTableData();
};
const handleSizeChange = (val: number) => {
	state.current_page = 1;
	state.page_size = val;
	getTableData();
};
// 查询
const search = () => {
	state.total_page = 1;
	state.total_cnt = 0;
	state.current_page = 1;
  state.sort_mode = '';
  multipleTableRef.value!.clearSort()
	getTableData();
};
// 重置
const resetData = () => {
	state.total_page = 1;
	state.total_cnt = 0;
	state.current_page = 1;
	state.userid = '';
	state.username = '';
	state.role = '';
	state.creatorid = '';
	state.creatorname = '';
	state.create_time = '';
  state.sort_mode = '';
  multipleTableRef.value!.clearSort()
	getTableData();
};
// 排序
const handleSortChange = (e) => {
	let { prop, order } = e;
	if (prop === 'rolename') {
		prop = 'role';
	}
	if (order === 'ascending') {
		state.sort_mode = prop + 'A';
	} else if (order === 'descending') {
		state.sort_mode = prop + 'B';
	}
	state.total_page = 1;
	state.total_cnt = 0;
	state.current_page = 1;
	getTableData();
};
// 页面加载时
onMounted(() => {
	getTableData();
});
</script>
<style lang="scss" scoped>
.el-card__body {
	padding: 20px 10px;
}
</style>
