declare type LeftTopListDataType = {
    // 统计名称
    title: string,
    // 统计数量
    num: number,
    // 变化类型 true: 增加
    changeType: boolean,
    // 变化幅度
    changeNum: number
}

// 左上数据
declare type LeftTopDataType = LeftTopListDataType[]



declare type LeftCenterChartDataType = {
    // 机构\AS 名称
    value: number,
    // 出口边界数量
    name: string
}

declare type LeftCenterRankDataType = {
    // 机构名称
    organization: string,
    // as名称
    as: string,
    // 边界数量
    boundaryNum: number,
    // 路径数量
    pathNum: number,
}

// 左中数据 - 出口边界数量
declare type LeftCenterDataType = {
    chartData: {
        organization: LeftCenterChartDataType[],
        as: LeftCenterChartDataType[]
    },
    rankData: {
        boundaryRank: LeftCenterRankDataType[],
        pathRank: LeftCenterRankDataType[]
    }
};



declare type LeftBottomCountryRankDataType = {
    // 地区国旗 flag-icon-二字码
    countryFlag: string,
    // 地区国名
    countryName: string,
    // 边界数量
    boundaryNum: number,
    // 路径数量
    path: number
}

declare type LeftBottomAsRankDataType = {
    // 地区国旗 flag-icon-二字码
    countryFlag: string,
    // 地区国名
    countryName: string,
    // as名称
    as: string,
    // 边界数量
    boundaryNum: number,
    // 路径数量
    path: number
}

// 左下数据 - 出口下一跳
declare type LeftBottomDataType = {
    country: {
        boundaryRank: LeftBottomCountryRankDataType[],
        pathRank: LeftBottomCountryRankDataType[]
    },
    as: {
        boundaryRank: LeftBottomAsRankDataType[],
        pathRank: LeftBottomAsRankDataType[],
    }
}



// 中上数据
declare type CenterTopDataType = {}



// 中下数据 - 从观测地区出发的到其他国家的关键路径
declare type CenterBottomDataType = {
    // 时间序列数据
    TimeData: [],
    // 变化数据
    NewData: [],
    DisconnectionData: [],
    ErrorData: []
}



declare type RightTopListDataType = {
    // 统计名称
    title: string,
    // 统计数量
    num: number,
    // 变化类型 true: 增加
    changeType: boolean,
    // 变化幅度
    changeNum: number
}

// 右上数据
declare type RightTopDataType = RightTopListDataType[]



declare type RightCenterChartDataType = {
    // 机构\AS 名称
    value: number,
    // 出口边界数量
    name: string
}

declare type RightCenterRankDataType = {
    // 机构名称
    organization: string,
    // as名称
    as: string,
    // 边界数量
    boundaryNum: number,
    // 路径数量
    pathNum: number,
}

// 左中数据 - 入口边界数量
declare type RightCenterDataType = {
    chartData: {
        organization: RightCenterChartDataType[],
        as: RightCenterChartDataType[]
    },
    rankData: {
        boundaryRank: RightCenterRankDataType[],
        pathRank: RightCenterRankDataType[]
    }
};



declare type RightBottomCountryRankDataType = {
    // 地区国旗 flag-icon-二字码
    countryFlag: string,
    // 地区国名
    countryName: string,
    // 边界数量
    boundaryNum: number,
    // 路径数量
    path: number
}

declare type RightBottomAsRankDataType = {
    // 地区国旗 flag-icon-二字码
    countryFlag: string,
    // 地区国名
    countryName: string,
    // as名称
    as: string,
    // 边界数量
    boundaryNum: number,
    // 路径数量
    path: number
}

// 左下数据 - 入口下一跳
declare type RightBottomDataType = {
    country: {
        boundaryRank: RightBottomCountryRankDataType[],
        pathRank: RightBottomCountryRankDataType[]
    },
    as: {
        boundaryRank: RightBottomAsRankDataType[],
        pathRank: RightBottomAsRankDataType[],
    }
}
