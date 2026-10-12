from smoke_app_import import app

class Service:
    @staticmethod
    def select_market_best(snap,h,n=5):
        return sorted(snap['stocks'],key=lambda x:x['horizons'][h]['ranking_score'],reverse=True)[:n]


def make(code,a,b,c):
    return {'ticker':code,'name':code,'price':100,'horizons':{h:{'ranking_score':score,'entry_timing':{'score':75,'action':'等待買點'},'plan':{'zone_low':98,'zone_high':102,'trigger':103,'invalidation':94}} for h,score in zip(('short','mid','long'),(a,b,c))}}


def test():
    old_service=app.service
    old_next=app.rank_next_session
    app.service=Service()
    app.rank_next_session=lambda snap,horizon,n=5: Service.select_market_best(snap,horizon,n=n)
    try:
        snap={'price_date':'2026-10-08','stocks':[make('A',95,90,35),make('B',82,81,80),make('C',50,92,91),make('D',70,70,70)]}
        df=app._board_filtered_rows(snap, 3,'完整日線','全部',True)
        assert set(df['代號'])=={'A','B','C','D'}
        df2=app._board_filtered_rows(snap,3,'完整日線','短線',False)
        assert len(df2)==3 and df2['週期'].eq(app.H_LABEL['short']).all()
        assert len(app._board_filtered_rows(snap,3,'明日關注','長線',False))==3
        assert app._board_filtered_rows(snap,3,'全部','全部',True)['代號'].nunique()==4
        print('PASS V17.3 board period, category, consensus, duplicate prevention')
    finally:
        app.service=old_service
        app.rank_next_session=old_next
if __name__=='__main__':test()
