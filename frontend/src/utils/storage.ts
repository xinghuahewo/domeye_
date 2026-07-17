import Cookies from 'js-cookie';

type StoredToken = {
	token: string;
	expire?: number;
};

type CachedUserInfo = {
	token: string;
	userInfo: {
		roles: string[];
		username: string;
		userid: string;
		password: string;
	};
};

const USER_INFO_CACHE_KEY = 'userInfo';

const parseStorageValue = <T>(value: string | null): T | null => {
	if (!value) return null;
	try {
		return JSON.parse(value) as T;
	} catch {
		return null;
	}
};

/**
 * window.localStorage 浏览器永久缓存
 * @method set 设置永久缓存
 * @method get 获取永久缓存
 * @method remove 移除永久缓存
 * @method clear 移除全部永久缓存
 */
export const Local = {
	// 查看 v2.4.3版本更新日志
	setKey(key: string) {
		// @ts-ignore
		return `${__NEXT_NAME__}:${key}`;
	},
	// 设置永久缓存
	set<T>(key: string, val: T) {
		window.localStorage.setItem(Local.setKey(key), JSON.stringify(val));
	},
	// 获取永久缓存
	get(key: string) {
		let json = <string>window.localStorage.getItem(Local.setKey(key));
		return JSON.parse(json);
	},
	// 移除永久缓存
	remove(key: string) {
		window.localStorage.removeItem(Local.setKey(key));
	},
	// 移除全部永久缓存
	clear() {
		window.localStorage.clear();
	},
};

export const TokenStorage = {
	get() {
		return parseStorageValue<StoredToken>(window.localStorage.getItem('token'));
	},
	getValue() {
		return TokenStorage.get()?.token || '';
	},
};

/**
 * window.sessionStorage 浏览器临时缓存
 * @method set 设置临时缓存
 * @method get 获取临时缓存
 * @method remove 移除临时缓存
 * @method clear 移除全部临时缓存
 */
export const Session = {
	// 设置临时缓存
	set<T>(key: string, val: T) {
		if (key === 'token') return Cookies.set(key, val);
		window.sessionStorage.setItem(Local.setKey(key), JSON.stringify(val));
	},
	// 获取临时缓存
	get(key: string) {
		if (key === 'token') return Cookies.get(key);
		let json = <string>window.sessionStorage.getItem(Local.setKey(key));
		return JSON.parse(json);
	},
	// 移除临时缓存
	remove(key: string) {
		if (key === 'token') return Cookies.remove(key);
		window.sessionStorage.removeItem(Local.setKey(key));
	},
	// 移除全部临时缓存
	clear() {
		Cookies.remove('token');
		window.sessionStorage.clear();
	},
};

export const UserInfoCache = {
	set(userInfo: CachedUserInfo['userInfo']) {
		const token = TokenStorage.getValue();
		if (!token) return UserInfoCache.clear();
		Session.set(USER_INFO_CACHE_KEY, { token, userInfo });
	},
	get() {
		const token = TokenStorage.getValue();
		if (!token) {
			UserInfoCache.clear();
			return null;
		}
		const cached = Session.get(USER_INFO_CACHE_KEY) as CachedUserInfo | null;
		if (!cached || cached.token !== token) {
			UserInfoCache.clear();
			return null;
		}
		return cached.userInfo;
	},
	clear() {
		Session.remove(USER_INFO_CACHE_KEY);
	},
};
