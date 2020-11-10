## EFPN implementation based on detectron2

"detectron2" folder name changed to "detectron" to correctly build (could also work outside of root dir)

add training script 

add "efpn.py" in detectron/modeling/backbone

get C2' from backbone to produce P2', connect new head to P2'\

loss function that balances foreground-background


Credits:

EFPN original paper: https://arxiv.org/pdf/2003.07021v1.pdf (Deng 2020)


```BibTeX
@misc{wu2019detectron2,
  author =       {Yuxin Wu and Alexander Kirillov and Francisco Massa and
                  Wan-Yen Lo and Ross Girshick},
  title =        {Detectron2},
  howpublished = {\url{https://github.com/facebookresearch/detectron2}},
  year =         {2019}
}
```
