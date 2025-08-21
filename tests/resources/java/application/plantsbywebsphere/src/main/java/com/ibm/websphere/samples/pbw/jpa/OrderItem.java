//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2003,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.jpa;

import javax.persistence.Column;
import javax.persistence.Embeddable;
import javax.persistence.EmbeddedId;
import javax.persistence.Entity;
import javax.persistence.JoinColumn;
import javax.persistence.ManyToOne;
import javax.persistence.NamedQueries;
import javax.persistence.NamedQuery;
import javax.persistence.Table;
import javax.persistence.Transient;

import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Bean mapping for the ORDERITEM table.
 */
@Entity(name = "OrderItem")
@Table(name = "ORDERITEM", schema = "APP")
@NamedQueries({ @NamedQuery(name = "removeAllOrderItem", query = "delete from OrderItem") })
public class OrderItem {
	/**
	 * Composite Key class for Entity Bean: OrderItem
	 * 
	 * Key consists of essentially two foreign key relations, but is mapped as foreign keys.
	 */
	@Embeddable
	public static class PK implements java.io.Serializable {
		static final long serialVersionUID = 3206093459760846163L;
		@Column(name = "inventoryID")
		public String inventoryID;
		@Column(name = "ORDER_ORDERID")
		public String order_orderID;

		public PK() {
			Util.debug("OrderItem.PK()");
		}

		public PK(String inventoryID, String argOrder) {
			Util.debug("OrderItem.PK() inventoryID=" + inventoryID + "=");
			Util.debug("OrderItem.PK() orderID=" + argOrder + "=");
			this.inventoryID = inventoryID;
			this.order_orderID = argOrder;
		}

		/**
		 * Returns true if both keys are equal.
		 */
		public boolean equals(java.lang.Object otherKey) {
			if (otherKey instanceof PK) {
				PK o = (PK) otherKey;
				return ((this.inventoryID.equals(o.inventoryID)) && (this.order_orderID.equals(o.order_orderID)));
			}
			return false;
		}

		/**
		 * Returns the hash code for the key.
		 */
		public int hashCode() {
			Util.debug("OrderItem.PK.hashCode() inventoryID=" + inventoryID + "=");
			Util.debug("OrderItem.PK.hashCode() orderID=" + order_orderID + "=");

			return (inventoryID.hashCode() + order_orderID.hashCode());
		}
	}

	@SuppressWarnings("unused")
	@EmbeddedId
	private OrderItem.PK id;
	private String name;
	private String pkginfo;
	private float price;
	private float cost;
	private int category;
	private int quantity;
	private String sellDate;
	@Transient
	private String inventoryId;

	@ManyToOne
	@JoinColumn(name = "INVENTORYID", insertable = false, updatable = false)
	private Inventory inventory;
	@ManyToOne
	@JoinColumn(name = "ORDER_ORDERID", insertable = false, updatable = false)
	private Order order;

	public int getCategory() {
		return category;
	}

	public void setCategory(int category) {
		this.category = category;
	}

	public float getCost() {
		return cost;
	}

	public void setCost(float cost) {
		this.cost = cost;
	}

	public String getName() {
		return name;
	}

	public void setName(String name) {
		this.name = name;
	}

	public String getPkginfo() {
		return pkginfo;
	}

	public void setPkginfo(String pkginfo) {
		this.pkginfo = pkginfo;
	}

	public float getPrice() {
		return price;
	}

	public void setPrice(float price) {
		this.price = price;
	}

	public int getQuantity() {
		return quantity;
	}

	public void setQuantity(int quantity) {
		this.quantity = quantity;
	}

	public String getSellDate() {
		return sellDate;
	}

	public void setSellDate(String sellDate) {
		this.sellDate = sellDate;
	}

	public OrderItem() {
	}

	public OrderItem(Inventory inv) {
		Util.debug("OrderItem(inv) - id = " + inv.getInventoryId());
		setInventoryId(inv.getInventoryId());
		inventory = inv;
		name = inv.getName();
		pkginfo = inv.getPkginfo();
		price = inv.getPrice();
		cost = inv.getCost();
		category = inv.getCategory();
	}

	public OrderItem(Order order, String orderID, Inventory inv, java.lang.String name, java.lang.String pkginfo,
			float price, float cost, int quantity, int category, java.lang.String sellDate) {
		Util.debug("OrderItem(etc.)");
		inventory = inv;
		setInventoryId(inv.getInventoryId());
		setName(name);
		setPkginfo(pkginfo);
		setPrice(price);
		setCost(cost);
		setQuantity(quantity);
		setCategory(category);
		setSellDate(sellDate);
		setOrder(order);
		id = new OrderItem.PK(inv.getInventoryId(), order.getOrderID());
	}

	/*
	 * updates the primary key field with the composite orderId+inventoryId
	 */
	public void updatePK() {
		id = new OrderItem.PK(inventoryId, order.getOrderID());
	}

	public Inventory getInventory() {
		return inventory;
	}

	public void setInventory(Inventory inv) {
		this.inventory = inv;
	}

	public Order getOrder() {
		return order;
	}

	/**
	 * Sets the order for this item Also updates the sellDate
	 * 
	 * @param order
	 */
	public void setOrder(Order order) {
		this.order = order;
		this.sellDate = order.getSellDate();
	}

	public String getInventoryId() {
		return inventoryId;
	}

	public void setInventoryId(String inventoryId) {
		this.inventoryId = inventoryId;
	}

}