<template>
	<div class="layout-navbars-breadcrumb-user pr15" :style="{ flex: layoutUserFlexNum }">
		<!-- <el-dropdown :show-timeout="70" :hide-timeout="50" trigger="click" @command="onComponentSizeChange">
			<div class="layout-navbars-breadcrumb-user-icon">
				<i class="iconfont icon-ziti" :title="$t('message.user.title0')"></i>
			</div>
			<template #dropdown>
				<el-dropdown-menu>
					<el-dropdown-item command="large" :disabled="state.disabledSize === 'large'">{{ $t('message.user.dropdownLarge') }}</el-dropdown-item>
					<el-dropdown-item command="default" :disabled="state.disabledSize === 'default'">{{ $t('message.user.dropdownDefault') }}</el-dropdown-item>
					<el-dropdown-item command="small" :disabled="state.disabledSize === 'small'">{{ $t('message.user.dropdownSmall') }}</el-dropdown-item>
				</el-dropdown-menu>
			</template>
		</el-dropdown>
		<el-dropdown :show-timeout="70" :hide-timeout="50" trigger="click" @command="onLanguageChange">
			<div class="layout-navbars-breadcrumb-user-icon">
				<i
					class="iconfont"
					:class="state.disabledI18n === 'en' ? 'icon-fuhao-yingwen' : 'icon-fuhao-zhongwen'"
					:title="$t('message.user.title1')"
				></i>
			</div>
			<template #dropdown>
				<el-dropdown-menu>
					<el-dropdown-item command="zh-cn" :disabled="state.disabledI18n === 'zh-cn'">简体中文</el-dropdown-item>
					<el-dropdown-item command="en" :disabled="state.disabledI18n === 'en'">English</el-dropdown-item>
					<el-dropdown-item command="zh-tw" :disabled="state.disabledI18n === 'zh-tw'">繁體中文</el-dropdown-item>
				</el-dropdown-menu>
			</template>
		</el-dropdown>
		<div class="layout-navbars-breadcrumb-user-icon" @click="onSearchClick">
			<el-icon :title="$t('message.user.title2')">
				<ele-Search />
			</el-icon>
		</div>
		<div class="layout-navbars-breadcrumb-user-icon" @click="onLayoutSetingClick">
			<i class="icon-skin iconfont" :title="$t('message.user.title3')"></i>
		</div>
		<div class="layout-navbars-breadcrumb-user-icon">
			<el-popover placement="bottom" trigger="click" transition="el-zoom-in-top" :width="300" :persistent="false">
				<template #reference>
					<el-badge :is-dot="true">
						<el-icon :title="$t('message.user.title4')">
							<ele-Bell />
						</el-icon>
					</el-badge>
				</template>
				<template #default>
					<UserNews />
				</template>
			</el-popover>
		</div> -->
		<div v-if="isHomeRoute" class="layout-navbars-breadcrumb-user-search mr10">
			<el-input
				v-model="state.knowledgeKeyword"
				size="small"
				clearable
				placeholder="知识库查询"
				@keyup.enter="submitKnowledgeSearch"
			>
				<template #append>
					<el-button size="small" @click="submitKnowledgeSearch">查询</el-button>
				</template>
			</el-input>
		</div>
		<div class="layout-navbars-breadcrumb-user-icon mr10" @click="onScreenfullClick">
			<i
				class="iconfont"
				:title="state.isScreenfull ? $t('message.user.title6') : $t('message.user.title5')"
				:class="!state.isScreenfull ? 'icon-fullscreen' : 'icon-tuichuquanping'"
			></i>
		</div>
		<el-dropdown :show-timeout="70" :hide-timeout="50" @command="onHandleCommandClick">
			<span class="layout-navbars-breadcrumb-user-link" style="line-height: 20px">
				<el-icon class="mr5"><ele-Avatar /></el-icon>
				{{ userInfo.userid }}
				<el-icon class="el-icon--right">
					<ele-ArrowDown />
				</el-icon>
			</span>
			<template #dropdown>
				<el-dropdown-menu>
					<el-dropdown-item command="/home">{{ $t('message.user.dropdown1') }}</el-dropdown-item>
					<!-- <el-dropdown-item command="wareHouse">{{ $t('message.user.dropdown6') }}</el-dropdown-item> -->
					<!-- <el-dropdown-item command="/personal">{{ $t('message.user.dropdown2') }}</el-dropdown-item> -->
					<!-- <el-dropdown-item command="/404">{{ $t('message.user.dropdown3') }}</el-dropdown-item>
					<el-dropdown-item command="/401">{{ $t('message.user.dropdown4') }}</el-dropdown-item> -->
					<el-dropdown-item divided @click="handleEdit()">用户信息</el-dropdown-item>
					<el-dropdown-item divided command="logOut">退出登录</el-dropdown-item>
				</el-dropdown-menu>
			</template>
		</el-dropdown>
	</div>
	<el-dialog v-model="state.editDialogVisible" title="用户信息" width="30%">
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
				<el-select v-model="editruleForm.role" disabled>
					<el-option label="管理员" value="admin" />
					<el-option label="操作员" value="operator" />
					<el-option label="访客" value="guest" />
				</el-select>
			</el-form-item>
		</el-form>
		<template #footer>
			<span class="dialog-footer">
				<el-button @click="editresetForm(editruleFormRef)">取消</el-button>
				<el-button type="primary" @click="editsubmitForm(editruleFormRef)">确认修改</el-button>
			</span>
		</template>
	</el-dialog>
</template>

<script setup lang="ts" name="layoutBreadcrumbUser">
import { ref, computed, reactive, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessageBox, ElMessage } from 'element-plus';
import screenfull from 'screenfull';
import { useI18n } from 'vue-i18n';
import { storeToRefs } from 'pinia';
import { useUserInfo } from '/@/stores/userInfo';
import { useThemeConfig } from '/@/stores/themeConfig';
import { Session, Local } from '/@/utils/storage';
import type { FormInstance, FormRules } from 'element-plus';
import request from '/@/utils/request';
import pinia from '/@/stores/index';

import baseUrl from "/@/api";
const state = reactive({
	isScreenfull: false,
	disabledI18n: 'zh-cn',
	disabledSize: 'default',
	editDialogVisible: false,
	knowledgeKeyword: '',
});
// 定义变量内容
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const stores = useUserInfo();
const storesThemeConfig = useThemeConfig();
const { userInfo } = storeToRefs(stores);
const { themeConfig } = storeToRefs(storesThemeConfig);
const isHomeRoute = computed(() => route.name === 'home');
//编辑用户
const editruleFormRef = ref<FormInstance>();
const editruleForm = reactive({
  userid: '',
	username: '',
	password: '',
	role: '',
});
const validatePassword = (rule: any, value: string, callback: any) => {
  const trim_value = value.trim().toLowerCase();
	if (trim_value.length < 6) {
		callback(new Error('密码至少为6位'));
	} else if (/[a-z]/.test(trim_value) && /[0-9]/.test(trim_value)){
		callback();
	} else{
    callback(new Error('密码必须同时包含数字和字母'));
  }
};
const editrules = reactive<FormRules>({
  userid: [{ required: true, message: '账号不能为空', trigger: 'blur' }],
	username: [{ required: true, message: '用户名不能为空', trigger: 'blur' }],
	password: [{ required: true,  validator: validatePassword, trigger: 'blur' }],
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
    // url: 'http://10.3.242.226:19746/profile',
		url: baseUrl + 'profile',
		method: 'post',
		data: {
			username: editruleForm.username,
			password: editruleForm.password,
		},
	});
	if (updateResult.status) {
		ElMessage.success(`用户信息修改成功`);
    await useUserInfo(pinia).setUserInfos();
	} else {
		ElMessage.error(`用户信息修改失败`);
	}
};
const handleEdit = () => {
	editruleForm.userid = userInfo.value.userid;
	editruleForm.username = userInfo.value.username;
	editruleForm.role = userInfo.value.roles[0];
	editruleForm.password = userInfo.value.password;
	state.editDialogVisible = true;
};


// 设置分割样式
const layoutUserFlexNum = computed(() => {
	let num: string | number = '';
	const { layout, isClassicSplitMenu } = themeConfig.value;
	const layoutArr: string[] = ['defaults', 'columns'];
	if (layoutArr.includes(layout) || (layout === 'classic' && !isClassicSplitMenu)) num = '1';
	else num = '';
	return num;
});
// 全屏点击时
const onScreenfullClick = () => {
	if (!screenfull.isEnabled) {
		ElMessage.warning('暂不不支持全屏');
		return false;
	}
	screenfull.toggle();
	screenfull.on('change', () => {
		if (screenfull.isFullscreen) state.isScreenfull = true;
		else state.isScreenfull = false;
	});
};
const submitKnowledgeSearch = () => {
	ElMessage.info('知识库搜索功能建设中，暂未接入检索接口。');
};
// 布局配置 icon 点击时
// const onLayoutSetingClick = () => {
// 	mittBus.emit('openSetingsDrawer');
// };
// 下拉菜单点击时
const onHandleCommandClick = (path: string) => {
	if (path === 'logOut') {
		ElMessageBox({
			closeOnClickModal: false,
			closeOnPressEscape: false,
			title: t('message.user.logOutTitle'),
			message: t('message.user.logOutMessage'),
			showCancelButton: true,
			confirmButtonText: t('message.user.logOutConfirm'),
			cancelButtonText: t('message.user.logOutCancel'),
			buttonSize: 'default',
			beforeClose: (action, instance, done) => {
				if (action === 'confirm') {
					instance.confirmButtonLoading = true;
					instance.confirmButtonText = t('message.user.logOutExit');
					setTimeout(() => {
						done();
						setTimeout(() => {
							instance.confirmButtonLoading = false;
						}, 300);
					}, 700);
				} else {
					done();
				}
			},
		})
			.then(async () => {
				// 清除缓存/token等
				Session.clear();
        localStorage.removeItem('token')
				// 使用 reload 时，不需要调用 resetRoute() 重置路由
				window.location.reload();
			})
			.catch(() => {});
	} else if (path === '/home') {
    router.push(path);
	} 
};
// 初始化组件大小/i18n
const initI18nOrSize = (value: string, attr: string) => {
	state[attr] = Local.get('themeConfig')[value];
};
// 页面加载时
onMounted(() => {
	if (Local.get('themeConfig')) {
		initI18nOrSize('globalComponentSize', 'disabledSize');
		initI18nOrSize('globalI18n', 'disabledI18n');
	}
});
</script>

<style scoped lang="scss">
.layout-navbars-breadcrumb-user {
	display: flex;
	align-items: center;
	justify-content: flex-end;
	&-search {
		width: 280px;
		display: flex;
		align-items: center;
	}
	&-link {
		height: 100%;
		display: flex;
		align-items: center;
		white-space: nowrap;
		&-photo {
			width: 25px;
			height: 25px;
			border-radius: 100%;
		}
	}
	&-icon {
		padding: 0 10px;
		cursor: pointer;
		color: var(--next-bg-topBarColor);
		height: 50px;
		line-height: 50px;
		display: flex;
		align-items: center;
		&:hover {
			background: var(--next-color-user-hover);
			i {
				display: inline-block;
				animation: logoAnimation 0.3s ease-in-out;
			}
		}
	}
	:deep(.el-dropdown) {
		color: var(--next-bg-topBarColor);
	}
	:deep(.el-badge) {
		height: 40px;
		line-height: 40px;
		display: flex;
		align-items: center;
	}
	:deep(.el-badge__content.is-fixed) {
		top: 12px;
	}
	/* 顶部搜索输入与按钮统一高度 */
	:deep(.layout-navbars-breadcrumb-user-search .el-input__wrapper) {
		min-height: 32px;
		height: 32px;
	}
	:deep(.layout-navbars-breadcrumb-user-search .el-input__inner) {
		height: 32px;
		line-height: 32px;
	}
	:deep(.layout-navbars-breadcrumb-user-search .el-input-group__append .el-button) {
		padding: 0 14px;
		min-height: 32px;
		height: 32px;
	}
}

@media (max-width: 992px) {
	.layout-navbars-breadcrumb-user {
		&-search {
			width: 220px;
		}
	}
}

@media (max-width: 768px) {
	.layout-navbars-breadcrumb-user {
		&-search {
			display: none;
		}
	}
}
</style>
