<template>
	<el-form size="large" class="login-content-form">
		<el-form-item class="login-animation1">
			<el-input text placeholder="请输入账号" v-model="state.ruleForm.userid" clearable autocomplete="off">
				<template #prefix>
					<el-icon class="el-input__icon"><ele-User /></el-icon>
				</template>
			</el-input>
		</el-form-item>
		<el-form-item class="login-animation2">
			<el-input :type="state.isShowPassword ? 'text' : 'password'" placeholder="请输入密码" v-model="state.ruleForm.password" autocomplete="off">
				<template #prefix>
					<el-icon class="el-input__icon"><ele-Unlock /></el-icon>
				</template>
				<template #suffix>
					<i
						class="iconfont el-input__icon login-content-password"
						:class="state.isShowPassword ? 'icon-yincangmima' : 'icon-xianshimima'"
						@click="state.isShowPassword = !state.isShowPassword"
					>
					</i>
				</template>
			</el-input>
		</el-form-item>
		<el-form-item class="login-animation3">
			<div style="display: flex">
				<el-input text maxlength="4" placeholder="请输入验证码" v-model="state.ruleForm.code" clearable autocomplete="off">
					<template #prefix>
						<el-icon class="el-input__icon"><ele-Position /></el-icon>
					</template>
				</el-input>
				<canvas id="canvas" @click="handleCanvas" width="100" height="40"> </canvas>
			</div>
		</el-form-item>
    <el-form-item class="login-animation4">
      <span style="color: gray; padding-right: 20px; margin-left: auto;">一周内免登录</span>
			<el-switch v-model="state.week" />
		</el-form-item>
		<el-form-item class="login-animation5">
			<el-button type="primary" class="login-content-submit" round v-waves @click="onSignIn" :loading="state.loading.signIn">
				<span>登录</span>
			</el-button>
		</el-form-item>
	</el-form>
</template>

<script setup lang="ts" name="loginAccount">
import { reactive, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { initFrontEndControlRoutes } from '/@/router/frontEnd';
import { NextLoading } from '/@/utils/loading';
import request from '/@/utils/request';
import { Session } from '/@/utils/storage';

// 定义变量内容
const router = useRouter();
const state = reactive({
	isShowPassword: false,
	ruleForm: {
		userid: '',
		password: '',
		code: '',
		true_code: '',
	},
	loading: {
		signIn: false,
	},
  week: false
});
// 绘制验证码
const draw = () => {
	let show_num = [];
	let canvas_width = document.querySelector('#canvas').clientWidth;
	let canvas_height = document.querySelector('#canvas').clientHeight;
	let canvas = document.getElementById('canvas'); //获取到canvas
	let context = canvas.getContext('2d'); //获取到canvas画图
	canvas.width = canvas_width;
	canvas.height = canvas_height;
	let sCode = 'a,b,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z,A,B,C,E,F,G,H,J,K,L,M,N,P,Q,R,S,T,W,X,Y,Z,1,2,3,4,5,6,7,8,9,0';
	let aCode = sCode.split(',');
	let aLength = aCode.length; //获取到数组的长度

	//4个验证码数
	for (let i = 0; i <= 3; i++) {
		let j = Math.floor(Math.random() * aLength); //获取到随机的索引值
		let deg = (Math.random() * 30 * Math.PI) / 180; //产生0~30之间的随机弧度
		let txt = aCode[j]; //得到随机的一个内容
		show_num[i] = txt.toLowerCase(); // 依次把取得的内容放到数组里面
		let x = 10 + i * 20; //文字在canvas上的x坐标
		let y = 20 + Math.random() * 8; //文字在canvas上的y坐标
		context.font = 'bold 23px 微软雅黑';

		context.translate(x, y);
		context.rotate(deg);

		context.fillStyle = randomColor();
		context.fillText(txt, 0, 0);

		context.rotate(-deg);
		context.translate(-x, -y);
	}
	//验证码上显示6条线条
	for (let i = 0; i <= 5; i++) {
		context.strokeStyle = randomColor();
		context.beginPath();
		context.moveTo(Math.random() * canvas_width, Math.random() * canvas_height);
		context.lineTo(Math.random() * canvas_width, Math.random() * canvas_height);
		context.stroke();
	}
	//验证码上显示31个小点
	for (let i = 0; i <= 30; i++) {
		context.strokeStyle = randomColor();
		context.beginPath();
		let x = Math.random() * canvas_width;
		let y = Math.random() * canvas_height;
		context.moveTo(x, y);
		context.lineTo(x + 1, y + 1);
		context.stroke();
	}

	//最后把取得的验证码数组存起来，方式不唯一
	let num = show_num.join('');
	// console.log(num);
	state.ruleForm.true_code = num;
};
//得到随机的颜色值
const randomColor = () => {
	var r = Math.floor(Math.random() * 256);
	var g = Math.floor(Math.random() * 256);
	var b = Math.floor(Math.random() * 256);
	return 'rgb(' + r + ',' + g + ',' + b + ')';
};
//canvas点击刷新
const handleCanvas = () => {
	draw();
};
onMounted(() => {
	draw();
});
// 验证并登录
const onSignIn = () => {
	const { userid, password, code, true_code } = state.ruleForm;
	if (userid.trim() === '') {
		ElMessage.error('账号不能为空');
	} else if (password.trim() === '') {
		ElMessage.error('密码不能为空');
	} else if (code.trim() === '') {
		ElMessage.error('验证码不能为空');
	} else if (code.toLowerCase() !== true_code) {
		ElMessage.error('验证码错误');
	} else {
		signIn();
	}
};
// 登录
const signIn = async () => {
	state.loading.signIn = true;
	// 请求登录接口
	try {
		const loginResult = await request({
			url: 'login',
			method: 'post',
			data: {
				userid: state.ruleForm.userid,
				password: state.ruleForm.password,
			},
		});
		if (!loginResult.status) {
			if (loginResult.msg) {
				ElMessage.error(loginResult.msg);
			} else {
				ElMessage.error('账号或密码错误');
			}
		} else {
			// 存储 token 到浏览器缓存
			localStorage.setItem('token', JSON.stringify({
				expire: state.week ? new Date().getTime()+7*24*60*60*1000 : new Date().getTime(),
				token: loginResult.token,
			}));
      Session.set('token', loginResult.token)
			await initFrontEndControlRoutes();
			signInSuccess(loginResult.msg);
		}
	} catch (e) {
		console.log(e);
	} finally {
		state.loading.signIn = false;
	}
};

// 登录成功后的跳转
const signInSuccess = (msg) => {
	// 登录成功，跳到转首页
	// 如果是复制粘贴的路径，非首页/登录页，那么登录成功后重定向到对应的路径中
	// if (route.query?.redirect) {
	// 	router.push({
	// 		path: <string>route.query?.redirect,
	// 		query: Object.keys(<string>route.query?.params).length > 0 ? JSON.parse(<string>route.query?.params) : '',
	// 	});
	// } else {
	router.push('/');
	// }
	// 登录成功提示
	if (msg) {
		ElMessage.success(msg);
	}
	// 添加 loading，防止第一次进入界面时出现短暂空白
	NextLoading.start();
	state.loading.signIn = false;
};
</script>

<style scoped lang="scss">
.login-content-form {
	margin-top: 20px;
	@for $i from 1 through 5 {
		.login-animation#{$i} {
			opacity: 0;
			animation-name: error-num;
			animation-duration: 0.5s;
			animation-fill-mode: forwards;
			animation-delay: calc($i/10) + s;
		}
	}
	.login-content-password {
		display: inline-block;
		width: 20px;
		cursor: pointer;
		&:hover {
			color: #909399;
		}
	}
	.login-content-code {
		width: 100%;
		padding: 0;
		font-weight: bold;
		letter-spacing: 5px;
	}
	.login-content-submit {
		width: 100%;
		letter-spacing: 2px;
		font-weight: 300;
	}
}

#canvas {
	margin-left: 10px;
	border-radius: 4px;
	box-shadow: 0 0 0 1px var(--el-input-border-color, var(--el-border-color)) inset;
}
</style>
