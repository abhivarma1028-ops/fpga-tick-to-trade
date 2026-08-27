/* ---------------------------------------------------------------------------
 * sw_latency_bench.c — COMPILED (C) software tick-to-trade latency baseline.
 *
 * A faithful port of the bit-identical golden chain
 *     order_book_m2  ->  strategy_imbalance  ->  risk_check
 * (the same model the RTL is verified against, see sim/golden/*.py). It times
 * the per-message compute on the CPU with clock_gettime(CLOCK_MONOTONIC), the
 * fair "compiled software" competitor to the FPGA fabric — far faster than the
 * Python baseline, so the comparison can't be dismissed as interpreter overhead.
 *
 * Input: a framed ITCH stream file (2-byte big-endian length prefix per msg),
 * identical bytes to the Python path. Generate it with:
 *     python3 -c "import sys;sys.path.insert(0,'sim');import replay_gen;\
 *         open('/tmp/itch_stream.bin','wb').write(replay_gen.build_synthetic(3000,1))"
 *
 * Build & run:  see host/run_c_bench.sh
 *
 * Scoping matches the Python bench: messages are pre-parsed; only apply ->
 * snapshot -> evaluate -> risk is timed (parse excluded -> optimistic for SW).
 * clock_gettime overhead is measured and subtracted per sample.
 * ------------------------------------------------------------------------- */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

/* ---- parser ---- */
enum { ADD=0x41, EXEC_PRICE=0x43, DELETE=0x44, EXECUTE=0x45, ADD_MPID=0x46,
       TRADE=0x50, REPLACE=0x55, CANCEL=0x58 };

typedef struct {
    uint8_t  type;
    uint64_t ref;
    int      side;       /* 0=buy 1=sell */
    uint32_t shares;
    uint32_t price;
    uint64_t new_ref;
} Msg;

static uint32_t be32(const uint8_t *p){ return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }
static uint64_t be64(const uint8_t *p){ uint64_t v=0; for(int i=0;i<8;i++) v=(v<<8)|p[i]; return v; }

/* parse one raw message (no length prefix); returns 1 if supported */
static int parse_message(const uint8_t *r, int len, Msg *m){
    if(len<1) return 0;
    uint8_t t=r[0];
    switch(t){case ADD:case EXEC_PRICE:case DELETE:case EXECUTE:case ADD_MPID:
              case TRADE:case REPLACE:case CANCEL: break; default: return 0;}
    m->type=t; m->side=0; m->shares=0; m->price=0;
    m->ref = (len>=19)? be64(r+11):0;
    m->new_ref = m->ref;
    if(t==ADD||t==ADD_MPID||t==TRADE){
        m->side = (r[19]=='S')?1:0;
        m->shares = be32(r+20);
        m->price  = be32(r+32);
    } else if(t==EXEC_PRICE){
        m->shares = be32(r+19);
        m->price  = be32(r+32);
    } else if(t==REPLACE){
        m->new_ref = be64(r+19);
        m->shares  = be32(r+27);
        m->price   = be32(r+31);
    } else if(t==CANCEL){
        m->shares = be32(r+19);
    } else if(t==EXECUTE){
        m->shares = be32(r+19);
    } /* DELETE: nothing extra */
    return 1;
}

/* ---- order book M2 ---- */
#define DEPTH 256
#define MASKV 255
#define NL 4

typedef struct { uint64_t ref; int side; uint32_t price; int64_t shares; } Entry;
typedef struct { int is_bid; uint32_t price[NL]; int64_t size[NL]; int cnt; } Side;

typedef struct {
    Entry entries[DEPTH];
    int   valid[DEPTH];
    Side  bid, ask;
} Book;

static void side_init(Side *s,int is_bid){ memset(s,0,sizeof(*s)); s->is_bid=is_bid; }

static void side_insert(Side *s, uint32_t p, int64_t sz){
    for(int i=0;i<NL;i++) if(i<s->cnt && s->price[i]==p){ s->size[i]+=sz; return; }
    int ins=s->cnt;
    for(int i=0;i<NL;i++){
        if(i<s->cnt){
            int better = s->is_bid ? (p>s->price[i]) : (p<s->price[i]);
            if(better){ ins=i; break; }
        }
    }
    if(ins<NL){
        for(int i=NL-1;i>ins;i--){ s->price[i]=s->price[i-1]; s->size[i]=s->size[i-1]; }
        s->price[ins]=p; s->size[ins]=sz;
        if(s->cnt<NL) s->cnt++;
    }
}
static uint32_t side_worst(Side *s){ return s->cnt? s->price[s->cnt-1] : 0; }

static void book_init(Book *b){ memset(b,0,sizeof(*b)); side_init(&b->bid,1); side_init(&b->ask,0); }

static void book_rescan(Book *b){
    side_init(&b->bid,1); side_init(&b->ask,0);
    for(int i=0;i<DEPTH;i++) if(b->valid[i]){
        Entry *e=&b->entries[i];
        if(e->side==0) side_insert(&b->bid,e->price,e->shares);
        else           side_insert(&b->ask,e->price,e->shares);
    }
}
static int affects_displayed(Book *b, Entry *e){
    if(e->side==1) return (b->ask.cnt!=NL) || (e->price <= side_worst(&b->ask));
    else           return (b->bid.cnt!=NL) || (e->price >= side_worst(&b->bid));
}

static void book_apply(Book *b, const Msg *m){
    int idx = (int)(m->ref & MASKV);
    if(m->type==ADD||m->type==ADD_MPID){
        b->entries[idx]=(Entry){m->ref,m->side,m->price,(int64_t)m->shares};
        b->valid[idx]=1;
        if(m->side==0) side_insert(&b->bid,m->price,(int64_t)m->shares);
        else           side_insert(&b->ask,m->price,(int64_t)m->shares);
        return;
    }
    if(m->type==TRADE) return;
    if(!(b->valid[idx] && b->entries[idx].ref==m->ref)) return; /* lk_match fail */

    Entry orig=b->entries[idx];
    int is_reduce = (m->type==CANCEL||m->type==EXECUTE||m->type==EXEC_PRICE);
    int is_delete = (m->type==DELETE);
    int is_repl   = (m->type==REPLACE);
    int rescan = is_repl || affects_displayed(b,&orig);

    if(is_reduce){
        if(orig.shares > (int64_t)m->shares) b->entries[idx].shares = orig.shares-(int64_t)m->shares;
        else b->valid[idx]=0;
    } else if(is_delete){
        b->valid[idx]=0;
    } else if(is_repl){
        int nidx=(int)(m->new_ref & MASKV);
        b->valid[idx]=0;
        b->entries[nidx]=(Entry){m->new_ref,orig.side,m->price,(int64_t)m->shares};
        b->valid[nidx]=1;
    }
    if(rescan) book_rescan(b);
}

/* snapshot fields */
typedef struct {
    uint32_t bbp, bap; int64_t bb_levels[NL], ba_levels[NL]; int book_valid;
} Snap;

static void book_snapshot(Book *b, Snap *s){
    s->bbp = b->bid.cnt? b->bid.price[0] : 0u;
    s->bap = b->ask.cnt? b->ask.price[0] : 0xFFFFFFFFu;
    s->book_valid = (b->bid.cnt>0 && b->ask.cnt>0);
    for(int i=0;i<NL;i++){ s->bb_levels[i]= i<b->bid.cnt? b->bid.size[i]:0;
                           s->ba_levels[i]= i<b->ask.cnt? b->ask.size[i]:0; }
}

/* ---- strategy imbalance ---- */
#define BID_THRESH 15
#define ASK_THRESH 15
#define MAX_SPREAD 1000
#define BASE_LOT 100
#define MAX_LOT  250

typedef struct { int prev_valid; } Strat;

static int64_t weighted(const int64_t *lv){ int64_t w=0; for(int i=0;i<NL;i++) w+=(int64_t)(NL-i)*lv[i]; return w; }
static int64_t lot(int64_t dom,int64_t oth,int64_t thresh){
    int64_t size;
    if(dom*10 > 3*thresh*oth)      size=3*BASE_LOT;
    else if(dom*10 > 2*thresh*oth) size=2*BASE_LOT;
    else                           size=BASE_LOT;
    return size<MAX_LOT? size:MAX_LOT;
}
/* returns 1 if a decision is produced; fills action/price/size */
static int strat_eval(Strat *st, const Snap *s, int *action, uint32_t *price, int64_t *size){
    int normal = s->bap > s->bbp;
    int64_t spread = normal? (int64_t)s->bap - (int64_t)s->bbp : 0;
    int elig = s->book_valid && st->prev_valid && normal && spread<=MAX_SPREAD;
    st->prev_valid = s->book_valid;
    if(!elig) return 0;
    int64_t wb=weighted(s->bb_levels), wa=weighted(s->ba_levels);
    int buy  = (wb*10 > (int64_t)BID_THRESH*wa) && (s->bap>0);
    int sell = (wa*10 > (int64_t)ASK_THRESH*wb) && (s->bbp>0);
    if(buy){  *action=0; *price=s->bap; *size=lot(wb,wa,BID_THRESH); return 1; }
    if(sell){ *action=1; *price=s->bbp; *size=lot(wa,wb,ASK_THRESH); return 1; }
    return 0;
}

/* ---- risk check ---- */
#define MAX_ORDER_SIZE 500
#define MAX_POSITION   1000
#define MAX_PRICE_BAND 5000
typedef struct { int64_t position; } Risk;

static int risk_check(Risk *rk, int action, uint32_t price, int64_t size, uint32_t ref){
    int64_t dev = (int64_t)price-(int64_t)ref; if(dev<0) dev=-dev;
    int64_t sq = action? -size : size;
    int64_t np = rk->position + sq;
    int ok = (size<=MAX_ORDER_SIZE) && (dev<=MAX_PRICE_BAND) &&
             ((np<0?-np:np)<=MAX_POSITION);
    if(ok) rk->position=np;
    return ok;
}

/* ---- timing helpers ---- */
static inline uint64_t now_ns(void){ struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000ull + (uint64_t)t.tv_nsec; }
static int cmp_u64(const void *a,const void *b){ uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;
    return (x>y)-(x<y); }

#define HW_LAT_NS 205

int main(int argc,char**argv){
    const char *path = argc>1? argv[1] : "/tmp/itch_stream.bin";
    int repeats = argc>2? atoi(argv[2]) : 50;

    FILE *f=fopen(path,"rb"); if(!f){ fprintf(stderr,"cannot open %s\n",path); return 2; }
    fseek(f,0,SEEK_END); long fsz=ftell(f); fseek(f,0,SEEK_SET);
    uint8_t *buf=malloc(fsz); if(fread(buf,1,fsz,f)!=(size_t)fsz){ return 2; } fclose(f);

    /* pre-parse (excluded from timing, like the Python bench) */
    Msg *msgs=malloc(sizeof(Msg)*(fsz/4+1)); int nm=0;
    long off=0;
    while(off+2<=fsz){
        int len=(buf[off]<<8)|buf[off+1]; off+=2;
        if(off+len>fsz) break;
        if(parse_message(buf+off,len,&msgs[nm])) nm++;
        off+=len;
    }

    /* measure clock_gettime overhead (min of back-to-back calls) */
    uint64_t ovh=~0ull;
    for(int i=0;i<10000;i++){ uint64_t a=now_ns(),b=now_ns(); if(b-a<ovh) ovh=b-a; }

    long total=(long)nm*repeats;
    uint64_t *lat=malloc(sizeof(uint64_t)*total); long k=0;

    /* faithfulness pass: count accepted decisions + final position (compare to
       Python phase2_golden stats to prove the C port is the same computation) */
    { Book b; book_init(&b); Strat st={0}; Risk rk={0}; long acc=0;
      for(int i=0;i<nm;i++){ book_apply(&b,&msgs[i]); Snap s; book_snapshot(&b,&s);
        int a; uint32_t p; int64_t z; if(strat_eval(&st,&s,&a,&p,&z)){ uint32_t mid=(s.bbp+s.bap)>>1; if(risk_check(&rk,a,p,z,mid)) acc++; } }
      printf("  [verify] parsed=%d accepted=%ld final_position=%lld\n",nm,acc,(long long)rk.position); }

    /* warm up */
    { Book b; book_init(&b); Strat st={0}; Risk rk={0};
      for(int i=0;i<nm;i++){ book_apply(&b,&msgs[i]); Snap s; book_snapshot(&b,&s);
        int a; uint32_t p; int64_t z; if(strat_eval(&st,&s,&a,&p,&z)){ uint32_t mid=(s.bbp+s.bap)>>1; risk_check(&rk,a,p,z,mid);} } }

    for(int r=0;r<repeats;r++){
        Book b; book_init(&b); Strat st={0}; Risk rk={0};
        for(int i=0;i<nm;i++){
            uint64_t t0=now_ns();
            book_apply(&b,&msgs[i]);
            Snap s; book_snapshot(&b,&s);
            int a; uint32_t p; int64_t z;
            if(strat_eval(&st,&s,&a,&p,&z)){ uint32_t mid=(s.bbp+s.bap)>>1; risk_check(&rk,a,p,z,mid); }
            uint64_t t1=now_ns();
            uint64_t d = (t1-t0> ovh)? (t1-t0-ovh):0;   /* subtract clock overhead */
            lat[k++]=d;
        }
    }

    qsort(lat,total,sizeof(uint64_t),cmp_u64);
    uint64_t mn=lat[0], mx=lat[total-1];
    uint64_t med=lat[total/2];
    uint64_t p90=lat[(long)(0.90*(total-1))];
    uint64_t p99=lat[(long)(0.99*(total-1))];
    double mean=0; for(long i=0;i<total;i++) mean+=lat[i]; mean/=total;

    printf("==================================================================\n");
    printf("  Software tick-to-trade latency  (COMPILED C, -O2)\n");
    printf("  stream=%s  messages=%d  samples=%ld (%d passes)\n",path,nm,total,repeats);
    printf("  clock_gettime overhead subtracted: %llu ns\n",(unsigned long long)ovh);
    printf("==================================================================\n");
    printf("  min     : %8llu ns\n",(unsigned long long)mn);
    printf("  median  : %8llu ns\n",(unsigned long long)med);
    printf("  mean    : %8.0f ns\n",mean);
    printf("  p90     : %8llu ns\n",(unsigned long long)p90);
    printf("  p99     : %8llu ns\n",(unsigned long long)p99);
    printf("  max     : %8llu ns\n",(unsigned long long)mx);
    printf("  jitter  : span(max-min)=%llu ns\n",(unsigned long long)(mx-mn));
    printf("------------------------------------------------------------------\n");
    printf("  Hardware (FPGA): %d ns, jitter 0 ns (41 cyc @ 200 MHz)\n",HW_LAT_NS);
    printf("  SPEEDUP FPGA vs C: %.1fx median, %.1fx p99\n",
           (double)med/HW_LAT_NS,(double)p99/HW_LAT_NS);
    printf("  JITTER  FPGA vs C: C spans %llu ns vs FPGA 0 ns (deterministic)\n",
           (unsigned long long)(mx-mn));
    printf("==================================================================\n");
    return 0;
}
