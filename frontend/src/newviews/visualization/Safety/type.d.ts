declare type LeftTopDataStatic = {
    // 事件名称
    name: string,
    // 时间数量
    value: number,
    itemStyle: {
        /*
            颜色设置
            已研判：#1a5dd4
                误报事件：#1ad45b
                异常事件：#d41a1a
                疑似事件：#d4831a
            待研判：rgb(153,169,191)
        */
        color: string
    },
    // 子事件
    children?: LeftTopDataStatic[]
}

// 左上数据
declare type LeftTopData = {
    world: {
        // 安全系数
        num: string,
        // 整体告警事件数量
        allStatic: number,
        // 整体告警事件数据: [已研判（误报事件，异常事件，疑似事件），待研判]
        static: LeftTopDataStatic[]
    },
    china: {
        num: string,
        allStatic: number,
        static: LeftTopDataStatic[]
    }
};



declare type thingsList = {
    // 事件数量
    value: number,
    // 事件类型
    name: string,
}

declare type LeftCenterListData = {
    // flag-icon-国家二字码
    flag: string
    // 所属国家
    attacked_country: string
    // 机构/AS 名称
    attacked: string
    // 事件总数量
    num: number
    // 事件详情数据
    thingsList: thingsList[]
}

// 左中数据
declare type LeftCenterData = {
    world: {
        // 机构异常事件排行数据
        organizationData: LeftCenterListData[],
        // AS异常事件排行数据
        asData: LeftCenterListData[]
    },
    china: {
        organizationData: LeftCenterListData[],
        asData: LeftCenterListData[]
    }
};



// 左下数据
declare type LeftBottomData = {
    world: {
        // 异常事件统计数据的时间序列
        timeData: string[],
        // 异常事件统计数据的事件数量
        numData: number[]
    },
    china: {
        timeData: [],
        numData: []
    }
}



declare type CenterTopWorldData = {
    // 地区名称
    name: string,
    // 经纬度
    lng: number,
    lat: number
    // 异常事件数量
    value: number,
}

// 中上数据
declare type CenterTopData = {
    // 全球异常事件
    world: CenterTopWorldData[],
    // 全国异常事件(我也不知道这个数据什么格式)
    china: {}
}



declare type CenterBottomThingsData = {
    // 受害AS所属地区国旗 flag-icon-二字码
    attackedFlag: string,
    // 受害AS所属地区国家名
    attackedCountry: string,
    // 受害AS所属机构名
    attackedOrg: string,
    // 受害AS名
    attackedAS: string,
    // 肇事AS所属地区国旗 flag-icon-二字码
    attackerFlag: string,
    // 肇事AS所属地区国家名
    attackerCountry: string,
    // 肇事AS所属机构名
    attackerOrg: string,
    // 肇事AS名
    attackerAS: string,
    // 开始时间 yyyy/mm/dd
    startTime: string,
    // 开始时间 yyyy/mm/dd
    endTime: string,
    // 是否研判 真：已研判
    eventJudge: boolean
    // 事件类型
    eventType: string,
    // 事件等级 高中低
    eventLevel: string
}

// 中下数据
declare type CenterBottomData = {
    // 全球事件轮播
    world: CenterBottomThingsData[],
    // 全国事件轮播
    china: CenterBottomThingsData[]
}



// 右上数据
declare type RightTopData = {
    // 全球事件态势概况
    world: {
        // 事件类型统计
        typeData: {
            // 路由劫持事件数量
            Hijacking: number,
            // 路由劫持事件变化幅度 5%
            HijackingAmplitude: string,
            // 路由劫持事件增长与否 true：增长
            HijackingAmplitudeType: boolean,
            // 中断事件
            Interrupt: number,
            InterruptAmplitude: string,
            InterruptAmplitudeType: boolean,
            // 路由泄露事件
            Divulge: number,
            DivulgeAmplitude: string,
            DivulgeAmplitudeType: boolean,
            // 国家中断事件
            Country: number,
            CountryAmplitude: string,
            CountryAmplitudeType: boolean,
        },
        // 事件等级统计
        levelData: {
            // 高等级事件
            highLevel: number,
            highLevelAmplitude: string,
            highLevelAmplitudeType: boolean,
            // 中等级事件
            middleLevel: number,
            middleLevelAmplitude: string,
            middleLevelAmplitudeType: boolean,
            // 低等级事件
            lowLevel: number,
            lowLevelAmplitude: string,
            lowLevelAmplitudeType: boolean,
        }
    },
    // 全国事件态势概况
    china: {
        typeData: {
            // 路由劫持事件数量
            Hijacking: number,
            // 路由劫持事件变化幅度 5%
            HijackingAmplitude: string,
            // 路由劫持事件增长与否 true：增长
            HijackingAmplitudeType: boolean,
            // 中断事件
            Interrupt: number,
            InterruptAmplitude: string,
            InterruptAmplitudeType: boolean,
            // 路由泄露事件
            Divulge: number,
            DivulgeAmplitude: string,
            DivulgeAmplitudeType: boolean,
            // 国家中断事件
            Country: number,
            CountryAmplitude: string,
            CountryAmplitudeType: boolean,
        },
        levelData: {
            // 高等级事件
            highLevel: number,
            highLevelAmplitude: string,
            highLevelAmplitudeType: boolean,
            // 中等级事件
            middleLevel: number,
            middleLevelAmplitude: string,
            middleLevelAmplitudeType: boolean,
            // 低等级事件
            lowLevel: number,
            lowLevelAmplitude: string,
            lowLevelAmplitudeType: boolean,
        }
    }
}



declare type RightCenterListData = {
    // flag-icon-国家二字码
    flag: string
    // 所属国家
    country: string
    // 机构名称
    organization: string
    // 资源数量
    num: number
}

// 右中数据
declare type RightCenterData = {
    world: {
        // 机构as资源排行
        AsData: RightCenterListData[],
        // 机构ip资源排行
        IPData: RightCenterListData[]
    },
    // 全国事件态势概况
    china: {
        AsData: RightCenterListData[],
        IPData: RightCenterListData[]
    }
}



declare type RightBottomListData = {
    // flag-icon-国家二字码
    flag: string
    // 所属国家
    country: string
    // 机构名称
    organization: string
    // AS名称
    as: string
    // 恶意分数
    num: number
}

// 右下数据
declare type RightBottomData = {
    // 恶意AS排行
    world: RightBottomListData[],
    china: RightBottomListData[]
}



declare type SafetyAllData = {
    LeftTopData: LeftTopData,
    LeftCenterData: LeftCenterData,
    LeftBottomData: LeftBottomData,
    CenterTopData: CenterTopData,
    CenterBottomData: CenterBottomData,
    RightTopData: RightTopData,
    RightCenterData: RightCenterData,
    RightBottomData: RightBottomData
}