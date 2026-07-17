import { defineStore } from 'pinia';
import request from '/@/utils/request';
import { UserInfoCache } from '/@/utils/storage';

const getDefaultUserInfo = () => ({
	username: '',
	userid: '',
	password: '',
	roles: [],
});

const formatUserInfo = (payload: any) => ({
	username: payload.username,
	userid: payload.userid,
	password: payload.password,
	roles: [payload.role],
});

/**
 * 用户信息
 * @methods setUserInfos 设置用户信息
 */
export const useUserInfo = defineStore('userInfo', {
 state: (): UserInfosState => ({
    userInfo: getDefaultUserInfo(),
  }),
  actions: {
		hydrateUserInfo() {
			const cachedUserInfo = UserInfoCache.get();
			if (!cachedUserInfo) return false;
			this.userInfo = cachedUserInfo;
			return true;
		},
		clearUserInfo() {
			this.userInfo = getDefaultUserInfo();
			UserInfoCache.clear();
		},
		async setUserInfos() {
			try {
				const res = await request({
					url: '/profile',
					method: 'get',
				});
				if (!res.status) {
					this.clearUserInfo();
					return false;
				}
				this.userInfo = formatUserInfo(res);
				UserInfoCache.set(this.userInfo);
				return true;
			} catch {
				this.clearUserInfo();
				return false;
			}
		},
  },
});
