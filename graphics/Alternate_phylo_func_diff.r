
ou5<-function(x){
  return(1-(1-sqrt(x))*exp(-x*6))
}

ou4<-function(x){
  return(1-(1-sqrt(x))*exp(-x))
}

ou6<-function(x){
  return(1-(1-sqrt(x))*exp(-x*3))
}

pdf("dist_plot.pdf")
x<-runif(1000,0,1)
y<-x
plot(y~x,type="n",xlab="Phylogenetic distance (time)",ylab="Functional distance (trait units)",xaxs="i", yaxs="i",cex.lab=2)
abline(a=0,b=1,lty=1,lwd=5,col="red")
curve(sqrt,from=0,to=1,add=TRUE,lwd=5,col="blue")
curve(ou5,from=0,to=1,add=TRUE,lwd=5,col="darkgreen")
curve(ou4,from=0,to=1,add=TRUE,lwd=5,col="darkgreen")
curve(ou6,from=0,to=1,add=TRUE,lwd=5,col="darkgreen")
dev.off()
